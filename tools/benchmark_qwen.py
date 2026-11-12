"""Benchmark local Qwen name transcription against original sample rows."""

import argparse
import json
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mkw_tracker.neural_ocr import NeuralNameReader  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='.omx/evidence/qwen-benchmark.json')
    parser.add_argument('frames', nargs='*', default=['.omx/evidence/frame-098.png', '.omx/evidence/frame-212.png'])
    args = parser.parse_args()
    reader = NeuralNameReader('qwen', timeout=180)
    observations = []
    try:
        for path in args.frames:
            frame = cv2.imread(path)
            if frame is None:
                raise ValueError(f'Cannot read frame: {path}')
            sy, sx = frame.shape[0] / 1152, frame.shape[1] / 2048
            crops = [frame[round((45 + i * 82) * sy):round((115 + i * 82) * sy),
                           round(1357 * sx):round(1755 * sx)] for i in range(12)]
            started = time.monotonic()
            rows = reader.recognize(crops)
            observation = {'frame': path, 'engine': 'qwen3-vl-8b',
                           'seconds': round(time.monotonic() - started, 3), 'rows': rows}
            observations.append(observation)
            print(json.dumps(observation, ensure_ascii=False), flush=True)
    finally:
        reader.close()
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(observations, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
