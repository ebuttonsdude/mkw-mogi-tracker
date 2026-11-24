import time
import unittest
from mkw_tracker.discord_rpc import DiscordPresence


class FakeIPC:
    def __init__(self):
        self.values = []
        self.closed = False

    def activity(self, activity):
        self.values.append(activity)

    def close(self):
        self.closed = True


class PresenceTests(unittest.TestCase):
    def wait_for(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(predicate())

    def test_disabled_never_connects(self):
        def forbidden(_):
            self.fail('disabled connection')
        presence = DiscordPresence(forbidden)
        presence.publish({'details': 'hello'})
        presence.configure(False, '')
        presence.close()
        self.assertEqual(presence.status(), 'Disabled')

    def test_publish_clear_and_restart(self):
        connections = []
        def connect(_):
            result = FakeIPC()
            connections.append(result)
            return result
        presence = DiscordPresence(connect, interval=.02, retry=.02)
        self.addCleanup(presence.close)
        presence.configure(True, '123')
        presence.publish({'details': 'one'})
        self.wait_for(lambda: connections and connections[0].values)
        self.assertEqual(connections[0].values[0], {'details': 'one'})
        presence.configure(False, '123')
        self.wait_for(lambda: connections[0].closed)
        self.assertIsNone(connections[0].values[-1])
        presence.configure(True, '123')
        presence.publish({'details': 'two'})
        self.wait_for(lambda: len(connections) == 2 and connections[1].values)
        presence.close()
        self.assertIsNone(connections[1].values[-1])
        self.assertTrue(connections[1].closed)

    def test_absent_discord_retries(self):
        attempts = []
        def absent(_):
            attempts.append(1)
            raise OSError('not running')
        presence = DiscordPresence(absent, retry=.02)
        self.addCleanup(presence.close)
        presence.configure(True, '123')
        presence.publish({'details': 'retry'})
        self.wait_for(lambda: len(attempts) >= 2)
        self.assertEqual(presence.status(), 'Waiting for Discord desktop')

    def test_slow_connection_publishes_latest_only(self):
        import threading
        entered, release = threading.Event(), threading.Event()
        connection = FakeIPC()
        def connect(_):
            entered.set()
            if not release.wait(2):
                raise OSError('test connection timed out')
            return connection
        presence = DiscordPresence(connect, interval=60)
        self.addCleanup(presence.close)
        self.addCleanup(release.set)
        presence.configure(True, '123')
        presence.publish({'details': 'old'})
        self.assertTrue(entered.wait(2))
        presence.publish({'details': 'new'})
        release.set()
        self.wait_for(lambda: connection.values)
        self.assertEqual(connection.values, [{'details': 'new'}])


class ProtocolTests(unittest.TestCase):
    def test_fragmented_frame_and_ping(self):
        import json
        import struct
        from mkw_tracker.discord_rpc import DiscordIPC
        body = json.dumps({'evt': 'READY'}).encode()
        wire = bytearray(struct.pack('<II', 3, 2) + b'hi' + struct.pack('<II', 1, len(body)) + body)
        class Socket:
            def recv(self, count):
                result = bytes(wire[:1])
                del wire[:1]
                return result
            def sendall(self, data):
                self.sent = data
        ipc = DiscordIPC.__new__(DiscordIPC)
        ipc.socket = Socket()
        self.assertEqual(ipc.receive(), {'evt': 'READY'})
        self.assertEqual(ipc.socket.sent, struct.pack('<II', 4, 2) + b'hi')

    def test_oversized_frame_rejected_before_body(self):
        import struct
        from mkw_tracker.discord_rpc import DiscordIPC, MAX_FRAME
        ipc = DiscordIPC.__new__(DiscordIPC)
        ipc._read = lambda count, deadline: struct.pack('<II', 1, MAX_FRAME + 1)
        with self.assertRaisesRegex(OSError, 'too large'):
            ipc.receive()

    def test_clear_omits_activity(self):
        from mkw_tracker.discord_rpc import DiscordIPC
        ipc = DiscordIPC.__new__(DiscordIPC)
        sent = []
        ipc.send = lambda opcode, body: sent.append(body)
        ipc.receive = lambda deadline=None: {'nonce': sent[-1]['nonce']}
        ipc.activity(None)
        self.assertNotIn('activity', sent[0]['args'])

    def test_activity_drains_unrelated_dispatch_with_shared_deadline(self):
        from mkw_tracker.discord_rpc import DiscordIPC
        ipc = DiscordIPC.__new__(DiscordIPC)
        sent, deadlines = [], []
        ipc.send = lambda opcode, body: sent.append(body)
        def receive(deadline):
            deadlines.append(deadline)
            return ({'cmd': 'DISPATCH', 'evt': 'OTHER'} if len(deadlines) == 1
                    else {'nonce': sent[-1]['nonce']})
        ipc.receive = receive
        ipc.activity({'details': 'hello'})
        self.assertEqual(len(deadlines), 2)
        self.assertEqual(deadlines[0], deadlines[1])

    def test_activity_dispatch_flood_is_bounded(self):
        from mkw_tracker.discord_rpc import DiscordIPC
        ipc = DiscordIPC.__new__(DiscordIPC)
        ipc.send = lambda opcode, body: None
        ipc.receive = lambda deadline: {'cmd': 'DISPATCH'}
        with self.assertRaisesRegex(OSError, 'acknowledgement missing'):
            ipc.activity({'details': 'hello'})


class WindowsPipeTests(unittest.TestCase):
    def api(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        return SimpleNamespace(**{key: Mock(return_value=value) for key, value in {
            'CreateFileW': 42, 'SetNamedPipeHandleState': 1, 'CloseHandle': 1,
            'ReadFile': 1, 'WriteFile': 1, 'GetLastError': 232}.items()})

    def test_native_byte_mode_read_partial_write_and_close(self):
        import ctypes
        from mkw_tracker.discord_rpc import WindowsPipe
        api = self.api()
        received = []
        def read(handle, buffer, count, transferred, overlapped):
            ctypes.memmove(buffer, b'abc', 3)
            transferred._obj.value = 3
            return 1
        def write(handle, buffer, count, transferred, overlapped):
            received.append(ctypes.string_at(buffer, 1))
            transferred._obj.value = 1
            return 1
        api.ReadFile.side_effect, api.WriteFile.side_effect = read, write
        pipe = WindowsPipe('test-pipe', api)
        self.assertEqual(api.SetNamedPipeHandleState.call_args.args[1]._obj.value, 1)
        self.assertEqual(pipe.recv(8), b'abc')
        pipe.sendall(b'xyz')
        self.assertEqual(received, [b'x', b'y', b'z'])
        pipe.close()
        pipe.close()
        api.CloseHandle.assert_called_once_with(42)

    def test_failed_nonblocking_setup_closes_handle(self):
        from mkw_tracker.discord_rpc import WindowsPipe
        api = self.api()
        api.SetNamedPipeHandleState.return_value = 0
        with self.assertRaises(OSError):
            WindowsPipe('test', api)
        api.CloseHandle.assert_called_once_with(42)

    def test_empty_pipe_timeout_and_broken_pipe(self):
        from unittest.mock import patch
        from mkw_tracker.discord_rpc import WindowsPipe
        api = self.api()
        api.ReadFile.return_value = 0
        pipe = WindowsPipe('test', api)
        self.addCleanup(pipe.close)
        with patch('mkw_tracker.discord_rpc.time.monotonic', side_effect=[0, 0, 1]), \
                patch('mkw_tracker.discord_rpc.time.sleep'):
            with self.assertRaisesRegex(OSError, 'timed out'):
                pipe.recv(4)
        api.GetLastError.return_value = 109
        with self.assertRaisesRegex(OSError, 'disconnected'):
            pipe.recv(4)

    def test_zero_write_times_out(self):
        from unittest.mock import patch
        from mkw_tracker.discord_rpc import WindowsPipe
        pipe = WindowsPipe('test', self.api())
        self.addCleanup(pipe.close)
        with patch('mkw_tracker.discord_rpc.time.monotonic', side_effect=[0, 0, 1]), \
                patch('mkw_tracker.discord_rpc.time.sleep'):
            with self.assertRaisesRegex(OSError, 'timed out'):
                pipe.sendall(b'hello')
