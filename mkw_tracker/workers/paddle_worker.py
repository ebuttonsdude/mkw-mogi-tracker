"""Persistent, isolated PaddleOCR recognition process (JSON lines on stdin/stdout)."""
from __future__ import annotations

import argparse
import base64
import contextlib
import json
import math
import os
import sys
from pathlib import Path

MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_REQUEST_BYTES = 12 * ((MAX_IMAGE_BYTES + 2) // 3 * 4 + 8) + 128
MAX_RESPONSE_BYTES = 1024 * 1024


class RecognitionWorker:
    def __init__(self, model_dir):
        self.model_dir = Path(model_dir)
        self.model = None

    def recognize(self, request):
        images = request.get('images') if isinstance(request, dict) else None
        if not isinstance(images, list) or not 1 <= len(images) <= 12:
            raise ValueError('images must contain between 1 and 12 base64 PNG strings')
        # Imports and model diagnostics must never corrupt the JSONL protocol.
        with contextlib.redirect_stdout(sys.stderr):
            import cv2
            import numpy as np

            decoded = []
            for value in images:
                if not isinstance(value, str) or len(value) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
                    raise ValueError('Each image must be a base64 PNG smaller than 4 MiB')
                data = base64.b64decode(value, validate=True)
                if len(data) >= MAX_IMAGE_BYTES or not data.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise ValueError('Each image must be a PNG smaller than 4 MiB')
                image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
                if image is None or image.size == 0:
                    raise ValueError('Unable to decode PNG image')
                if image.shape[0] > 2048 or image.shape[1] > 8192:
                    raise ValueError('Row image exceeds maximum dimensions')
                decoded.append(image)
            if self.model is None:
                if not self.model_dir.is_dir():
                    raise ValueError(f'Model directory does not exist: {self.model_dir}')
                os.environ.setdefault('PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK', 'True')
                from paddleocr import TextRecognition

                self.model = TextRecognition(
                    model_name='PP-OCRv5_server_rec', model_dir=str(self.model_dir),
                    device='cpu', enable_mkldnn=False,
                )
            results = list(self.model.predict(decoded, batch_size=12))
        if len(results) != len(images):
            raise RuntimeError('Recognition returned an unexpected number of rows')
        rows = []
        for result in results:
            score = float(result['rec_score'])
            if not math.isfinite(score):
                score = 0.0
            rows.append({'name': str(result['rec_text']), 'confidence': max(0.0, min(1.0, score))})
        return {'rows': rows, 'engine': 'paddle-ppocrv5'}


def serve(worker, source, destination):
    while True:
        line = source.readline(MAX_REQUEST_BYTES + 1)
        if not line:
            return
        try:
            if len(line) > MAX_REQUEST_BYTES:
                # Drain the oversized request so a following valid request works.
                while line and not line.endswith('\n'):
                    line = source.readline(MAX_REQUEST_BYTES + 1)
                raise ValueError('Request exceeds size limit')
            response = worker.recognize(json.loads(line))
        except Exception as exc:
            response = {'error': str(exc)}
        # ASCII-safe UTF-8 JSONL also survives Windows cp1252 text streams.
        message = json.dumps(response, ensure_ascii=True) + '\n'
        if len(message) > MAX_RESPONSE_BYTES:
            message = json.dumps({'error': 'Response exceeds size limit'}) + '\n'
        destination.write(message)
        destination.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', required=True)
    arguments = parser.parse_args()
    serve(RecognitionWorker(arguments.model_dir), sys.stdin, sys.stdout)


if __name__ == '__main__':
    main()
