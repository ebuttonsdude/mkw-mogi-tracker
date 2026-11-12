import base64
import json
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from mkw_tracker.obs import ObsClient, ObsError


def packet(op, **data):
    return json.dumps({'op': op, 'd': data})


class FakeConnection:
    def __init__(self, *messages):
        self.messages = list(messages)
        self.sent = []
        self.closed = False
        self.timeouts = []

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def send(self, raw):
        self.sent.append(json.loads(raw))

    def recv(self):
        message = self.messages.pop(0)
        if isinstance(message, Exception):
            raise message
        if callable(message):
            return message(self.sent[-1]['d'])
        return message

    def close(self):
        self.closed = True


def response(data=None, *, success=True, code=100, comment=None):
    def build(request):
        return packet(7, requestType=request['requestType'], requestId=request['requestId'],
                      requestStatus={'result': success, 'code': code, 'comment': comment},
                      responseData=data if data is not None else {})
    return build


class ObsTests(unittest.TestCase):
    def client(self, *messages, auth=None, password=''):
        hello = {'rpcVersion': 1}
        if auth is not None:
            hello['authentication'] = auth
        connection = FakeConnection(packet(0, **hello),
                                    packet(2, negotiatedRpcVersion=1), *messages)
        factory_calls = []

        def factory(url, **kwargs):
            factory_calls.append((url, kwargs))
            return connection

        client = ObsClient(password=password, connection_factory=factory)
        client.connect()
        self.assertEqual(factory_calls, [('ws://127.0.0.1:4455', {'timeout': 5.0})])
        self.assertTrue(all(0 < t <= 5 for t in connection.timeouts))
        return client, connection

    def test_authentication_with_documented_salt_and_challenge(self):
        client, connection = self.client(auth={
            'challenge': '+IxH4CnCiqpX1rM9scsNynZzbOe4KhDeYcTNS3PDaeY=',
            'salt': 'lM1GncleQOaCu9lT1yeUZhFYnqhsLLP1G5lAGo3ixaI=',
        }, password='supersecretpassword')
        self.assertEqual(connection.sent[0], {'op': 1, 'd': {
            'rpcVersion': 1, 'eventSubscriptions': 64,
            'authentication': '1Ct943GAT+6YQUUX47Ia/ncufilbe6+oD6lY+5kaCu4=',
        }})
        client.close()
        self.assertTrue(connection.closed)

    def test_interleaved_recording_events_and_correlated_requests(self):
        events = [
            {'eventType': 'RecordStateChanged', 'eventIntent': 64,
             'eventData': {'outputState': 'OBS_WEBSOCKET_OUTPUT_PAUSED'}},
            {'eventType': 'RecordFileChanged', 'eventIntent': 64,
             'eventData': {'newOutputPath': '/tmp/part2.mkv'}},
        ]
        status = {'outputActive': True, 'outputPaused': True,
                  'outputDuration': 1234, 'outputTimecode': '00:00:01.234'}
        client, connection = self.client(
            packet(5, **events[0]), packet(5, eventType='StreamStateChanged', eventData={}),
            packet(5, **events[1]), response(status), response({'sources': []}))
        self.assertEqual(client.record_status(), status)
        self.assertEqual(client.drain_events(), events)
        self.assertEqual(client.drain_events(), [])
        self.assertEqual(client.request('GetInputList', {}), {'sources': []})
        requests = connection.sent[1:]
        self.assertEqual(requests[0]['d']['requestType'], 'GetRecordStatus')
        self.assertNotEqual(requests[0]['d']['requestId'], requests[1]['d']['requestId'])
        self.assertNotIn('authentication', connection.sent[0]['d'])

    def test_server_error_preserves_connection_and_hides_secrets(self):
        client, connection = self.client(
            response(success=False, code=600, comment='secret-password'), response({'ok': True}),
            password='secret-password')
        with self.assertRaisesRegex(ObsError, '^OBS request failed with code 600$'):
            client.request('GetSourceScreenshot')
        self.assertFalse(connection.closed)
        self.assertEqual(client.request('GetVersion'), {'ok': True})

    def test_invalid_responses_close_connection(self):
        messages = ['not json', '[]', packet(99), packet(7, requestId='other'),
                    packet(5, eventType=None), '', TimeoutError('secret-password')]
        for message in messages:
            with self.subTest(message=message):
                client, connection = self.client(message, password='secret-password')
                with self.assertRaises(ObsError) as error:
                    client.request('GetVersion')
                self.assertNotIn('secret-password', str(error.exception))
                self.assertTrue(connection.closed)
                with self.assertRaisesRegex(ObsError, 'not connected'):
                    client.request('GetVersion')

    def test_event_stream_cannot_extend_request_deadline(self):
        event = packet(5, eventType='RecordStateChanged', eventData={})
        client, connection = self.client(event, event, response())
        with patch('mkw_tracker.obs.time.monotonic', side_effect=[0, 0.1, 6]):
            with self.assertRaisesRegex(ObsError, 'timed out'):
                client.request('GetRecordStatus')
        self.assertTrue(connection.closed)
        self.assertEqual(len(connection.messages), 2)

    def test_response_status_and_data_validated(self):
        def malformed(request):
            return packet(7, requestType=request['requestType'], requestId=request['requestId'],
                          requestStatus={'result': 'false', 'code': 100})
        client, connection = self.client(malformed)
        with self.assertRaisesRegex(ObsError, 'invalid request status'):
            client.request('GetVersion')
        self.assertTrue(connection.closed)
        client, connection = self.client(response([]))
        with self.assertRaisesRegex(ObsError, 'invalid response data'):
            client.request('GetVersion')

    def test_screenshot_requests_fast_high_quality_jpeg_at_native_resolution(self):
        expected = np.full((4, 7, 3), (12, 80, 230), dtype=np.uint8)
        ok, encoded = cv2.imencode('.jpg', expected, [cv2.IMWRITE_JPEG_QUALITY, 95])
        self.assertTrue(ok)
        data = 'data:image/jpeg;base64,' + base64.b64encode(encoded).decode()
        client, connection = self.client(response({'imageData': data}))
        actual = client.screenshot('Game capture')
        self.assertEqual(actual.shape, expected.shape)
        self.assertLessEqual(int(np.abs(actual.astype(int) - expected.astype(int)).max()), 3)
        self.assertEqual(connection.sent[-1]['d']['requestData'], {
            'sourceName': 'Game capture', 'imageFormat': 'jpeg', 'imageCompressionQuality': 95,
        })

    def test_screenshot_rejects_missing_or_corrupt_images(self):
        for data in (None, 123, 'wrong', 'data:image/jpeg;base64,!',
                     'data:image/jpeg;base64,', 'data:image/jpeg;base64,YWJj'):
            with self.subTest(data=data):
                client, _ = self.client(response({'imageData': data}))
                with self.assertRaises(ObsError):
                    client.screenshot('Game capture')

    def test_record_status_validation(self):
        valid = {'outputActive': False, 'outputPaused': False,
                 'outputDuration': 0, 'outputTimecode': '00:00:00.000'}
        for key, bad in [('outputActive', 0), ('outputPaused', None),
                         ('outputDuration', -1), ('outputDuration', float('nan')),
                         ('outputDuration', True), ('outputTimecode', 123)]:
            with self.subTest(key=key, bad=bad):
                client, _ = self.client(response({**valid, key: bad}))
                with self.assertRaisesRegex(ObsError, 'invalid recording status'):
                    client.record_status()

    def test_connect_failure_closes_and_sanitizes(self):
        for messages in [
            [packet(2)], [packet(0, rpcVersion=0)],
            [packet(0, rpcVersion=1, authentication={})],
            [packet(0, rpcVersion=1), packet(2, negotiatedRpcVersion=2)],
            [OSError('secret-password')],
        ]:
            with self.subTest(messages=messages):
                connection = FakeConnection(*messages)
                client = ObsClient(password='secret-password',
                                   connection_factory=lambda *args, **kwargs: connection)
                with self.assertRaises(ObsError) as error:
                    client.connect()
                self.assertNotIn('secret-password', str(error.exception))
                self.assertTrue(connection.closed)

    def test_only_loopback_endpoints_and_finite_timeouts(self):
        for url in ('ws://example.com:4455', 'ws://192.168.1.2:4455',
                    'http://127.0.0.1:4455', 'ws://user:secret@localhost:4455',
                    'ws://localhost:4455/?password=secret', 'ws://localhost'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                ObsClient(url)
        ObsClient('ws://[::1]:4455')
        ObsClient('ws://localhost:4455')
        for timeout in (0, -1, float('nan'), float('inf')):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                ObsClient(timeout=timeout)


if __name__ == '__main__':
    unittest.main()
