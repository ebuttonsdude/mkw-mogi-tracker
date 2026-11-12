import base64
import io
import json
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from tools.paddle_worker import RecognitionWorker, serve
from mkw_tracker.workers import paddle_worker


class FakeModel:
    def __init__(self):
        self.calls = 0

    def predict(self, images, batch_size):
        self.calls += 1
        print('model diagnostic')
        return [{'rec_text': 'てがすべってdc☆', 'rec_score': .9} for _ in images]


class PaddleWorkerTests(unittest.TestCase):
    def setUp(self):
        self.worker = RecognitionWorker('unused')
        self.worker.model = FakeModel()
        image = np.zeros((70, 398, 3), dtype=np.uint8)
        self.image = base64.b64encode(cv2.imencode('.png', image)[1]).decode()

    def test_unicode_and_loaded_model_survive_multiple_requests(self):
        source = io.StringIO((json.dumps({'images': [self.image]}) + '\n') * 2)
        destination = io.StringIO()
        serve(self.worker, source, destination)
        rows = [json.loads(line) for line in destination.getvalue().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['rows'][0]['name'], 'てがすべってdc☆')
        self.assertEqual(self.worker.model.calls, 2)

    def test_malformed_request_does_not_stop_next_request(self):
        source = io.StringIO('bad json\n' + json.dumps({'images': [self.image]}) + '\n')
        destination = io.StringIO()
        serve(self.worker, source, destination)
        responses = [json.loads(line) for line in destination.getvalue().splitlines()]
        self.assertIn('error', responses[0])
        self.assertEqual(responses[1]['engine'], 'paddle-ppocrv5')

    def test_bad_image_requests_do_not_invoke_model(self):
        for request in (None, {}, {'images': []}, {'images': [self.image] * 13},
                        {'images': ['not base64']}, {'images': [base64.b64encode(b'text').decode()]}):
            with self.subTest(request=type(request)), self.assertRaises(ValueError):
                self.worker.recognize(request)
        self.assertEqual(self.worker.model.calls, 0)

    def test_oversized_image_is_rejected(self):
        with self.assertRaises(ValueError):
            self.worker.recognize({'images': ['A' * (6 * 1024 * 1024)]})

    def test_oversized_line_is_drained_before_next_request(self):
        class StubWorker:
            def recognize(self, request):
                return {'ok': request}

        source = io.StringIO('x' * 100 + '\n{}\n')
        destination = io.StringIO()
        with patch.object(paddle_worker, 'MAX_REQUEST_BYTES', 20):
            serve(StubWorker(), source, destination)
        responses = [json.loads(line) for line in destination.getvalue().splitlines()]
        self.assertIn('error', responses[0])
        self.assertEqual(responses[1], {'ok': {}})

    def test_cp1252_stream_preserves_unicode_names_and_errors(self):
        name = 'Ren\u00e9 \u3066\u304c\u3059\u3079\u3063\u3066 \u03a9\u03b1 \u2606\u2665'

        class StubWorker:
            def recognize(self, request):
                if request.get('fail'):
                    raise ValueError(name)
                return {'rows': [{'name': name, 'confidence': .9}]}

        source = io.StringIO('{}\n{"fail": true}\n{}\n')
        with io.BytesIO() as buffer, io.TextIOWrapper(buffer, encoding='cp1252') as destination:
            serve(StubWorker(), source, destination)
            data = buffer.getvalue()
            self.assertTrue(data.isascii())
            responses = [json.loads(line) for line in data.decode('utf-8').splitlines()]
        self.assertEqual(responses[0]['rows'][0]['name'], name)
        self.assertEqual(responses[1]['error'], name)
        self.assertEqual(responses[2], responses[0])

    def test_oversized_response_and_error_remain_bounded_and_worker_survives(self):
        class StubWorker:
            def recognize(self, request):
                if request.get('fail'):
                    raise ValueError('\u2606' * 100)
                return {'name': '\u2606' * request.get('count', 0)}

        source = io.StringIO('{"count": 100}\n{"fail": true}\n{}\n')
        destination = io.StringIO()
        with patch.object(paddle_worker, 'MAX_RESPONSE_BYTES', 80):
            serve(StubWorker(), source, destination)
        lines = destination.getvalue().splitlines(keepends=True)
        self.assertTrue(all(len(line.encode('utf-8')) <= 80 for line in lines))
        self.assertEqual([json.loads(line) for line in lines], [
            {'error': 'Response exceeds size limit'}, {'error': 'Response exceeds size limit'}, {'name': ''}])


if __name__ == '__main__':
    unittest.main()
