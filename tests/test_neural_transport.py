"""Real pipe/process tests without model dependencies or capture hardware."""

import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from mkw_tracker import neural_ocr


NAME = 'Ren\u00e9 \u3066\u304c\u3059\u3079\u3063\u3066 \u03a9\u03b1 \u2606\u2665'
GOOD_WORKER = f'''
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({{'rows': [{{'name': {NAME!r}, 'confidence': .9}}
          for _ in request['images']]}}, ensure_ascii=True), flush=True)
'''


class NeuralTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.script = self.root / 'worker.py'
        self.script.write_text(GOOD_WORKER, encoding='utf-8')
        self.reader = neural_ocr.NeuralNameReader(python_path=sys.executable, model_dir=self.root, timeout=3)
        self.reader._script = self.script
        self.images = [np.zeros((12, 80, 3), dtype=np.uint8)]
        self.processes = []
        self.transports = []
        self.logs = []
        popen = subprocess.Popen
        transport = neural_ocr._WorkerTransport

        def launch(*args, **kwargs):
            self.logs.append(kwargs['stderr'])
            process = popen(*args, **kwargs)
            self.processes.append(process)
            return process

        def start_transport(process):
            result = transport(process)
            self.transports.append(result)
            return result

        for name, replacement in (('subprocess.Popen', launch), ('_WorkerTransport', start_transport)):
            patcher = patch('mkw_tracker.neural_ocr.' + name, side_effect=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.reader.close)

    def assert_stopped(self):
        self.assertIsNone(self.reader._process)
        self.assertIsNone(self.reader._transport)
        self.assertIsNone(self.reader._log)
        self.assertIsNone(self.reader._job)
        for process in self.processes:
            self.assertIsNotNone(process.poll())
            if sys.platform == 'win32':
                self.assertTrue(process._handle.closed)
            self.assertTrue(process.stdin.closed)
            self.assertTrue(process.stdout.closed)
        for transport in self.transports:
            self.assertFalse(transport.thread.is_alive())
        for log in self.logs:
            self.assertTrue(log.closed)

    def recognize_in_thread(self, images=None):
        errors = []

        def recognize():
            try:
                self.reader.recognize(self.images if images is None else images)
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=recognize)
        thread.start()
        self.addCleanup(thread.join, 5)
        return thread, errors

    def wait_for_marker(self):
        deadline = time.monotonic() + 3
        while not (self.root / 'started').exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue((self.root / 'started').exists())

    def test_reuses_single_pipe_owner_without_selectors_or_nonblocking_pipes(self):
        with patch('selectors.DefaultSelector', side_effect=AssertionError('Windows pipe selector')), \
                patch('os.set_blocking', side_effect=AssertionError('Nonblocking Windows pipe')):
            for _ in range(3):
                self.assertEqual(self.reader.recognize(self.images), [{'name': NAME, 'confidence': .9}])
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(len(self.transports), 1)
        self.assertEqual(self.transports[0].requests.maxsize, 1)
        self.assertEqual(self.transports[0].responses.maxsize, 2)
        self.reader.close()
        self.assert_stopped()
        self.reader.close()
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            self.reader.recognize(self.images)

    def test_concurrent_requests_are_serialized_on_one_worker(self):
        results, errors = [], []
        barrier = threading.Barrier(4)

        def recognize(count):
            try:
                barrier.wait(timeout=3)
                rows = self.reader.recognize(self.images * count)
                results.append((count, rows))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=recognize, args=(count,)) for count in range(1, 5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 4)
        for count, rows in results:
            self.assertEqual(rows, [{'name': NAME, 'confidence': .9}] * count)
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(len(self.transports), 1)

    def test_utf8_multibyte_response_can_span_reads(self):
        response = (json.dumps({'rows': [{'name': NAME, 'confidence': .9}]}, ensure_ascii=False) + '\n').encode('utf-8')
        boundary = response.index('\u3066'.encode('utf-8')) + 1
        self.script.write_text(
            'import os, sys, time\nsys.stdin.readline()\n'
            f'os.write(sys.stdout.fileno(), {response[:boundary]!r})\ntime.sleep(.05)\n'
            f'os.write(sys.stdout.fileno(), {response[boundary:]!r})\n', encoding='utf-8')
        self.assertEqual(self.reader.recognize(self.images), [{'name': NAME, 'confidence': .9}])

    def test_cp1252_child_serve_preserves_names_errors_and_persistent_reader(self):
        package_root = str(Path(neural_ocr.__file__).resolve().parent.parent)
        self.script.write_text(f'''
import sys
sys.path.insert(0, {package_root!r})
from mkw_tracker.workers.paddle_worker import serve
sys.stdin.reconfigure(encoding='cp1252')
sys.stdout.reconfigure(encoding='cp1252', errors='strict')
class Worker:
    def recognize(self, request):
        if len(request['images']) == 2:
            raise ValueError({NAME!r})
        return {{'rows': [{{'name': {NAME!r}, 'confidence': .9}}]}}
serve(Worker(), sys.stdin, sys.stdout)
''', encoding='utf-8')
        with patch.dict(os.environ, {'PYTHONIOENCODING': 'cp1252'}):
            for _ in range(2):
                self.assertEqual(self.reader.recognize(self.images), [{'name': NAME, 'confidence': .9}])
            self.assertEqual(len(self.processes), 1)
            with self.assertRaisesRegex(RuntimeError, NAME):
                self.reader.recognize(self.images * 2)
            self.assert_stopped()
            self.assertEqual(self.reader.recognize(self.images)[0]['name'], NAME)
        self.assertEqual(len(self.processes), 2)

    def test_eof_invalid_utf8_extra_data_and_oversize_reap_then_restart(self):
        good = json.dumps({'rows': [{'name': 'name', 'confidence': .9}]}).encode('utf-8')
        responses = [(b'', 'exited'), (b'\xff\n', None), (good + b'\nextra\n', 'extra response'),
                     (b'x' * (1024 * 1024 + 1), 'size limit')]
        for response, error in responses:
            with self.subTest(error=error):
                self.script.write_text('import sys\nsys.stdin.readline()\n'
                                       f'sys.stdout.buffer.write({response!r})\nsys.stdout.flush()\n', encoding='utf-8')
                with self.assertRaises((RuntimeError, ValueError)) as caught:
                    self.reader.recognize(self.images)
                if error:
                    self.assertIn(error, str(caught.exception))
                self.assert_stopped()
                self.script.write_text(GOOD_WORKER, encoding='utf-8')
                self.assertEqual(self.reader.recognize(self.images)[0]['name'], NAME)
                self.reader._close()

    def test_silent_and_partial_response_timeout_reaps_pipe_owner(self):
        for output in ('', '{'):
            with self.subTest(output=output):
                self.script.write_text('import sys,time\nsys.stdin.readline()\n'
                                       f'sys.stdout.write({output!r})\nsys.stdout.flush()\ntime.sleep(30)\n',
                                       encoding='utf-8')
                self.reader.timeout = .2
                started = time.monotonic()
                with self.assertRaisesRegex(TimeoutError, 'timed out'):
                    self.reader.recognize(self.images)
                self.assertLess(time.monotonic() - started, 3)
                self.assert_stopped()
        self.script.write_text(GOOD_WORKER, encoding='utf-8')
        self.reader.timeout = 3
        self.assertEqual(self.reader.recognize(self.images)[0]['name'], NAME)

    def test_request_timeout_also_bounds_a_worker_that_never_reads_stdin(self):
        self.script.write_text('import time\ntime.sleep(30)\n', encoding='utf-8')
        self.reader.timeout = .2
        with patch('cv2.imencode', return_value=(True, np.zeros(512 * 1024, dtype=np.uint8))):
            with self.assertRaisesRegex(TimeoutError, 'timed out'):
                self.reader.recognize(self.images)
        self.assert_stopped()

    def test_close_interrupts_blocked_request_write(self):
        self.script.write_text('import pathlib,time\n'
                               f'pathlib.Path({str(self.root / "started")!r}).touch()\ntime.sleep(30)\n',
                               encoding='utf-8')
        self.reader.timeout = 30
        with patch('cv2.imencode', return_value=(True, np.zeros(512 * 1024, dtype=np.uint8))):
            thread, errors = self.recognize_in_thread()
            self.wait_for_marker()
            started = time.monotonic()
            self.reader.close()
            thread.join(timeout=3)
        self.assertLess(time.monotonic() - started, 3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(errors)
        self.assert_stopped()

    def test_close_during_process_creation_reaps_unpublished_worker(self):
        creating, release = threading.Event(), threading.Event()
        launch = neural_ocr.subprocess.Popen.side_effect
        self.script.write_text('import pathlib,time\n'
                               f'pathlib.Path({str(self.root / "started")!r}).touch()\ntime.sleep(60)\n',
                               encoding='utf-8')

        def delayed_launch(*args, **kwargs):
            process = launch(*args, **kwargs)
            creating.set()
            release.wait(timeout=3)
            return process

        with patch('mkw_tracker.neural_ocr.subprocess.Popen', side_effect=delayed_launch):
            thread, errors = self.recognize_in_thread()
            self.assertTrue(creating.wait(timeout=3))
            if sys.platform == 'win32':
                self.assertIsNone(self.processes[0].poll())
                self.assertEqual(self.transports, [])
                self.assertFalse((self.root / 'started').exists())
            closing = threading.Thread(target=self.reader.close)
            closing.start()
            self.assertTrue(self.reader._closed.wait(timeout=3))
            release.set()
            closing.join(timeout=3)
            thread.join(timeout=3)
        self.assertFalse(closing.is_alive())
        self.assertFalse(thread.is_alive())
        self.assertTrue(errors)
        self.assert_stopped()
        if sys.platform == 'win32':
            # Keep Popen objects alive and prove their inherited stderr handles
            # cannot defer file cleanup until a later garbage collection.
            (self.root / 'logs/paddle-ocr.log').unlink()

    @unittest.skipIf(sys.platform == 'win32', 'Windows terminates the entire Job Object instead')
    def test_close_uses_owned_group_without_direct_process_signals(self):
        self.script.write_text('import sys,pathlib,time\nsys.stdin.readline()\n'
                               f'pathlib.Path({str(self.root / "started")!r}).touch()\ntime.sleep(30)\n',
                               encoding='utf-8')
        self.reader.timeout = 30
        thread, errors = self.recognize_in_thread()
        self.wait_for_marker()
        process = self.processes[0]
        with patch.object(process, 'terminate') as terminate, \
                patch.object(process, 'kill') as kill:
            self.reader.close()
        thread.join(timeout=3)
        terminate.assert_not_called()
        kill.assert_not_called()
        self.assertFalse(thread.is_alive())
        self.assertTrue(errors)
        self.assert_stopped()

    @unittest.skipUnless(sys.platform == 'linux', 'Linux process-group and pidfd regression')
    def test_linux_close_and_timeout_reap_descendant_holding_stdout(self):
        marker = self.root / 'started'
        for action in ('close', 'timeout', 'leader_exit'):
            with self.subTest(action=action):
                self.reader._closed.clear()
                marker.unlink(missing_ok=True)
                self.script.write_text(
                    'import os,pathlib,signal,subprocess,sys,time\n'
                    'child = subprocess.Popen([sys.executable, "-c", '
                    '"import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)"])\n'
                    f'marker = pathlib.Path({str(marker)!r})\n'
                    'pending = marker.with_suffix(".pending")\n'
                    'pending.write_text(str(child.pid))\n'
                    'pending.replace(marker)\n'
                    'sys.stdin.readline()\ntime.sleep(60)\n', encoding='utf-8')
                self.reader.timeout = 1 if action == 'timeout' else 30
                thread, errors = self.recognize_in_thread()
                self.wait_for_marker()
                pid = int(marker.read_text(encoding='utf-8'))
                descriptor = os.pidfd_open(pid)
                self.addCleanup(os.close, descriptor)
                # Emergency cleanup also runs on assertion failure.
                def kill_descendant(descriptor=descriptor):
                    try:
                        signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                self.addCleanup(kill_descendant)
                if action == 'leader_exit':
                    leader = self.processes[-1]
                    leader_fd = os.pidfd_open(leader.pid)
                    try:
                        signal.pidfd_send_signal(leader_fd, signal.SIGKILL)
                        os.waitid(os.P_PID, leader.pid, os.WEXITED | os.WNOWAIT)
                    finally:
                        os.close(leader_fd)
                started = time.monotonic()
                if action != 'timeout':
                    self.reader.close()
                thread.join(timeout=3)
                self.assertLess(time.monotonic() - started, 3)
                self.assertFalse(thread.is_alive())
                self.assertTrue(errors)
                import select
                self.assertEqual(select.select([descriptor], [], [], 2)[0], [descriptor])
                self.assert_stopped()

    def test_cleanup_reports_pipe_owner_that_did_not_exit(self):
        self.reader.recognize(self.images)
        transport = self.reader._transport
        with patch.object(transport.thread, 'join'), patch.object(transport.thread, 'is_alive', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'pipe owner did not exit'):
                self.reader._close()
            self.assertIs(self.reader._transport, transport)
            self.assertIsNone(self.reader._log)
        self.reader._close()
        self.assert_stopped()

    def test_job_failures_close_handle_and_finish_reader_cleanup(self):
        for failure in ('terminate', 'query', 'deadline'):
            with self.subTest(failure=failure):
                job = neural_ocr._WindowsWorkerJob.__new__(neural_ocr._WindowsWorkerJob)
                job.handle = 42
                job.kernel = Mock()
                job.kernel.TerminateJobObject.return_value = failure != 'terminate'
                job.kernel.QueryInformationJobObject.return_value = failure != 'query'
                job.kernel.CloseHandle.return_value = True
                job.accounting_type = lambda: SimpleNamespace(active=1)
                job.ctypes = SimpleNamespace(byref=lambda value: value, sizeof=lambda _: 48,
                                             get_last_error=lambda: 5, WinError=lambda _: OSError('job API failed'))
                self.reader.recognize(self.images)
                actual_job = self.reader._job
                self.reader._job = job
                # Limit the mocked clock to job.close; cleanup joins use real time.
                close = job.close
                def failing_close():
                    try:
                        with patch('mkw_tracker.neural_ocr.time.monotonic', side_effect=[0, 3]):
                            close()
                    finally:
                        if actual_job is not None:
                            actual_job.close()
                with patch.object(job, 'close', side_effect=failing_close):
                    with self.assertRaisesRegex((OSError, RuntimeError), 'job API failed|process tree did not exit'):
                        self.reader._close()
                job.kernel.CloseHandle.assert_called_once_with(42)
                self.assertIsNone(job.handle)
                self.assert_stopped()

    @unittest.skipUnless(sys.platform == 'win32', 'Native Windows Job Object containment')
    def test_windows_containment_failures_never_run_uncontained_worker(self):
        self.script.write_text('import pathlib,time\n'
                               f'pathlib.Path({str(self.root / "started")!r}).touch()\ntime.sleep(60)\n',
                               encoding='utf-8')
        for method in ('assign', 'resume'):
            with self.subTest(method=method), \
                    patch.object(neural_ocr._WindowsWorkerJob, method, side_effect=OSError('containment failed')):
                with self.assertRaisesRegex(OSError, 'containment failed'):
                    self.reader.recognize(self.images)
                self.assertFalse((self.root / 'started').exists())
                self.assert_stopped()

    @unittest.skipUnless(sys.platform == 'win32', 'Native Windows Job Object containment')
    def test_windows_close_and_timeout_reap_actual_worker_and_descendant(self):
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        marker = self.root / 'started'
        for action in ('close', 'timeout', 'launcher_exit', 'protocol_error', 'query_failure', 'terminate_failure'):
            with self.subTest(action=action):
                self.reader._closed.clear()
                marker.unlink(missing_ok=True)
                release = self.root / 'release'
                release.unlink(missing_ok=True)
                self.script.write_text(
                    'import json,os,pathlib,subprocess,sys,time\n'
                    'child = subprocess.Popen([sys.executable, "-c", '
                    '"import os,time;print(os.getpid(),flush=True);time.sleep(60)"], stdout=subprocess.PIPE)\n'
                    'child_pid = int(child.stdout.readline())\n'
                    f'marker = pathlib.Path({str(marker)!r})\n'
                    'pending = marker.with_suffix(".pending")\n'
                    'pending.write_text(json.dumps([os.getpid(), child_pid]))\n'
                    'pending.replace(marker)\n'
                    f'while not pathlib.Path({str(release)!r}).exists(): time.sleep(.01)\n'
                    'print("not JSON", flush=True)\n'
                    'time.sleep(60)\n', encoding='utf-8')
                self.reader.timeout = 2 if action == 'timeout' else 30
                thread, errors = self.recognize_in_thread()
                self.wait_for_marker()
                handles = []
                for pid in json.loads(marker.read_text(encoding='utf-8')):
                    handle = kernel.OpenProcess(0x00100000, False, pid)
                    self.assertTrue(handle)
                    self.addCleanup(kernel.CloseHandle, handle)
                    handles.append(handle)
                if action == 'launcher_exit':
                    self.processes[-1].kill()
                    self.processes[-1].wait(timeout=3)
                if action == 'protocol_error':
                    release.touch()
                elif action in ('query_failure', 'terminate_failure'):
                    operation = ('QueryInformationJobObject' if action == 'query_failure' else 'TerminateJobObject')
                    with patch.object(self.reader._job.kernel, operation, return_value=False):
                        self.reader.close()
                elif action != 'timeout':
                    self.reader.close()
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
                self.assertTrue(errors)
                for handle in handles:
                    self.assertEqual(kernel.WaitForSingleObject(handle, 2000), 0)
                self.assert_stopped()


@unittest.skipIf(sys.platform == 'win32', 'POSIX unreaped process-group identity')
class PosixWorkerIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.reader = neural_ocr.NeuralNameReader(python_path=sys.executable)
        self.events = []
        self.process = Mock(pid=1073741824, returncode=None)
        self.process.stdin = None
        self.process.stdout = None

        def reap(*args, **kwargs):
            self.events.append('reap')
            self.process.returncode = 0
            return 0

        # Popen.terminate() implicitly polls on POSIX, including during cancellation.
        self.process.poll.side_effect = reap
        self.process.wait.side_effect = reap
        self.process.terminate.side_effect = reap
        self.reader._process = self.process
        self.reader._posix_owner = (self.process, self.process.pid)
        # Install guards BEFORE cleanup registration: LIFO must close the
        # reader while every signal function is still intercepted.
        self.kill = self.enterContext(patch('mkw_tracker.neural_ocr.os.kill'))
        self.killpg = self.enterContext(patch('mkw_tracker.neural_ocr.os.killpg'))
        self.killpg.side_effect = lambda *args: self.events.append('group kill')
        self.waitid = self.enterContext(patch('mkw_tracker.neural_ocr.os.waitid', create=True))
        self.waitid.return_value = SimpleNamespace(si_pid=self.process.pid)
        self.getpgid = self.enterContext(patch('mkw_tracker.neural_ocr.os.getpgid',
                                              return_value=self.process.pid))
        self.getsid = self.enterContext(patch('mkw_tracker.neural_ocr.os.getsid',
                                             return_value=self.process.pid))
        self.addCleanup(self.reader.close)
        self.closing_threads = []
        # Never restore signal functions while a fake-process closer survives.
        self.addCleanup(self._join_closers)

    def _join_closers(self):
        for thread in self.closing_threads:
            if thread.ident is not None:
                thread.join()

    def test_cleanup_keeps_signal_interception_until_reader_is_closed(self):
        self.doCleanups()
        self.killpg.assert_called_once_with(1073741824, signal.SIGKILL)
        self.kill.assert_not_called()
        self.assertIsNone(self.reader._process)

    def test_failure_paths_keep_signal_guards_through_cleanup(self):
        for failure in ('setup', 'assertion', 'cleanup'):
            with self.subTest(failure=failure):
                class FailingCase(PosixWorkerIdentityTests):
                    def setUp(inner):
                        super().setUp()
                        if failure == 'setup':
                            raise RuntimeError('intentional setup failure')

                    def runTest(inner):
                        if failure == 'cleanup':
                            def fail_cleanup():
                                raise RuntimeError('intentional cleanup failure')
                            inner.addCleanup(fail_cleanup)
                        else:
                            inner.fail('intentional assertion failure')

                case = FailingCase('runTest')
                result = unittest.TestResult()
                case.run(result)
                self.assertEqual(len(result.errors) + len(result.failures), 1)
                case.killpg.assert_called_once_with(1073741824, signal.SIGKILL)
                case.kill.assert_not_called()
        # Nested guards must intercept cleanup, never the enclosing firewall.
        self.killpg.assert_not_called()
        self.kill.assert_not_called()

    def test_invalid_or_unowned_identity_never_signals(self):
        for pid in (0, 1, -1, True, '1073741824', Mock(), os.getpid(), os.getpgrp()):
            with self.subTest(pid=repr(pid)):
                self.process.pid = pid
                self.process.returncode = None
                self.reader._process = self.process
                self.reader._posix_owner = (self.process, pid)
                with self.assertRaisesRegex(RuntimeError, 'ownership'):
                    self.reader._close()
                self.assertIs(self.reader._process, self.process)
        self.process.pid = 1073741824
        self.reader._process = self.process
        self.reader._posix_owner = None
        with self.assertRaisesRegex(RuntimeError, 'ownership'):
            self.reader._close()
        self.reader._process = None  # Synthetic resource only; no OS process exists.
        self.killpg.assert_not_called()
        self.kill.assert_not_called()
        self.process.terminate.assert_not_called()
        self.process.kill.assert_not_called()

    def test_wrong_group_or_session_never_signals(self):
        for lookup in (self.getpgid, self.getsid):
            with self.subTest(lookup=lookup):
                self.reader._process = self.process
                self.reader._posix_owner = (self.process, self.process.pid)
                lookup.return_value = 1073741823
                try:
                    with self.assertRaisesRegex(RuntimeError, 'ownership'):
                        self.reader._close()
                finally:
                    lookup.return_value = self.process.pid
        self.reader._process = None  # Synthetic resource only.
        self.killpg.assert_not_called()
        self.kill.assert_not_called()

    def test_ownership_query_failure_blocks_reuse_and_keeps_resource(self):
        self.waitid.side_effect = PermissionError('ownership unavailable')
        try:
            with self.assertRaisesRegex(RuntimeError, 'ownership'):
                self.reader._start()
            self.assertIs(self.reader._process, self.process)
            self.killpg.assert_not_called()
            self.process.poll.assert_not_called()
        finally:
            self.waitid.side_effect = None

    def test_recovered_ownership_does_not_reuse_quarantined_worker(self):
        self.waitid.side_effect = PermissionError('ownership unavailable')
        try:
            with self.assertRaisesRegex(RuntimeError, 'ownership'):
                self.reader._close()
        finally:
            self.waitid.side_effect = None
        self.waitid.return_value = None
        with patch('mkw_tracker.neural_ocr.subprocess.Popen',
                   side_effect=OSError('replacement blocked')):
            with self.assertRaisesRegex(OSError, 'replacement blocked'):
                self.reader._start()
        self.killpg.assert_called_once_with(1073741824, signal.SIGKILL)
        self.assertIsNone(self.reader._process)

    def test_missing_posix_capability_rejects_before_spawning(self):
        self.reader._process = None
        self.reader._posix_owner = None
        original = neural_ocr.os
        limited = SimpleNamespace(**{name: getattr(original, name) for name in
                                    ('P_PID', 'WEXITED', 'WNOHANG', 'getpgid', 'getsid')})
        with patch.object(neural_ocr, 'os', limited), \
                patch.object(neural_ocr.subprocess, 'Popen',
                             side_effect=AssertionError('unexpected worker launch')) as launch:
            with self.assertRaisesRegex(RuntimeError, 'waitid/WNOWAIT'):
                self.reader._start()
            launch.assert_not_called()
        self.assertIsNone(self.reader._log)

    def test_exited_leader_is_not_reaped_by_liveness_before_group_teardown(self):
        with patch('mkw_tracker.neural_ocr.subprocess.Popen', side_effect=OSError('replacement blocked')):
            with self.assertRaisesRegex(OSError, 'replacement blocked'):
                self.reader._start()
        self.assertEqual(self.events[0], 'group kill')
        self.killpg.assert_called_once_with(self.process.pid, signal.SIGKILL)
        self.waitid.assert_any_call(os.P_PID, self.process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)

    def test_running_worker_liveness_does_not_poll(self):
        self.waitid.return_value = None
        with patch('mkw_tracker.neural_ocr.subprocess.Popen',
                   side_effect=AssertionError('unexpected replacement launch')) as launch:
            self.reader._start()
        self.assertEqual(self.events, [])
        self.process.poll.assert_not_called()
        launch.assert_not_called()
        self.reader._close()
        self.assertEqual(self.events[0], 'group kill')

    def test_already_reaped_leader_never_targets_numeric_group(self):
        self.process.returncode = 0
        self.reader._close()
        self.killpg.assert_not_called()
        self.process.terminate.assert_not_called()
        self.assertIsNone(self.reader._process)

    def test_externally_reaped_child_never_targets_numeric_group(self):
        self.waitid.side_effect = ChildProcessError('no longer our child')
        self.reader._close()
        self.killpg.assert_not_called()
        self.assertIsNone(self.reader._process)

    def test_cancellation_waits_for_owner_before_any_reaping_or_group_signal(self):
        lock = threading.Lock()
        waiting = threading.Event()

        class ObservedLock:
            acquire = lock.acquire
            release = lock.release

            def __enter__(self):
                waiting.set()
                lock.acquire()

            def __exit__(self, *args):
                lock.release()

        self.reader._lock = ObservedLock()
        errors = []

        def close():
            try:
                self.reader.close()
            except Exception as error:
                errors.append(error)

        lock.acquire()
        closing = threading.Thread(target=close)
        self.closing_threads.append(closing)
        closing.start()
        try:
            self.assertTrue(waiting.wait(timeout=3))
            self.assertTrue(self.reader._closed.is_set())
            self.assertEqual(self.events, [])
        finally:
            lock.release()
            closing.join(timeout=3)
        self.assertFalse(closing.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.events[0], 'group kill')
        self.killpg.assert_called_once_with(self.process.pid, signal.SIGKILL)

    def test_concurrent_cancellation_signals_group_only_once(self):
        killing, release = threading.Event(), threading.Event()
        errors = []

        def kill_group(*args):
            self.events.append('group kill')
            killing.set()
            if not release.wait(timeout=3):
                raise RuntimeError('test did not release group kill')

        def close():
            try:
                self.reader.close()
            except Exception as error:
                errors.append(error)

        self.killpg.side_effect = kill_group
        first = threading.Thread(target=close)
        second = threading.Thread(target=close)
        self.closing_threads.extend((first, second))
        first.start()
        try:
            self.assertTrue(killing.wait(timeout=3))
            second.start()
        finally:
            release.set()
            first.join(timeout=3)
            if second.ident is not None:
                second.join(timeout=3)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.killpg.assert_called_once_with(self.process.pid, signal.SIGKILL)


if __name__ == '__main__':
    unittest.main()
