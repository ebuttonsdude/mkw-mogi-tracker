"""Compare row preprocessing on the supplied results frames using one loaded model."""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import time
from pathlib import Path

import cv2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker-python', default='.local/paddle-venv/bin/python')
    parser.add_argument('--model-dir', default='.local/models/paddle-ppocrv5')
    parser.add_argument('--output', default='.omx/evidence/paddle-benchmark.json')
    parser.add_argument('frames', nargs='*', default=[
        '.omx/evidence/frame-098.png', '.omx/evidence/frame-212.png'])
    args = parser.parse_args()
    process = subprocess.Popen(
        [args.worker_python, 'tools/paddle_worker.py', '--model-dir', args.model_dir],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    evidence = []
    crop_dir = Path('.omx/evidence/ocr-crops')
    crop_dir.mkdir(parents=True, exist_ok=True)
    try:
        for path in args.frames:
            frame = cv2.imread(path)
            if frame is None:
                raise ValueError(f'Cannot read {path}')
            sy, sx = frame.shape[0] / 1152, frame.shape[1] / 2048
            crops = [frame[round((45 + i * 82) * sy):round((115 + i * 82) * sy),
                           round(1357 * sx):round(1755 * sx)] for i in range(12)]
            for variant in ('color', 'padded', 'upscaled', 'mask', 'tight', 'tight-padded'):
                batch = []
                for index, crop in enumerate(crops):
                    if variant in ('tight', 'tight-padded'):
                        crop = crop[round(15 * sy):round(59 * sy)]
                        if variant == 'tight-padded':
                            crop = cv2.copyMakeBorder(crop, 8, 8, 8, 8, cv2.BORDER_REPLICATE)
                    elif variant == 'padded':
                        crop = cv2.copyMakeBorder(crop, 10, 10, 10, 10, cv2.BORDER_REPLICATE)
                    elif variant == 'upscaled':
                        crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                    elif variant == 'mask':
                        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
                        crop = 255 - cv2.inRange(hsv, (0, 0, 175), (180, 110, 255))
                    cv2.imwrite(str(crop_dir / f'{Path(path).stem}-{variant}-{index + 1:02d}.png'), crop)
                    batch.append(base64.b64encode(cv2.imencode('.png', crop)[1]).decode())
                started = time.monotonic()
                process.stdin.write(json.dumps({'images': batch}) + '\n')
                process.stdin.flush()
                response = json.loads(process.stdout.readline())
                evidence.append({'frame': path, 'variant': variant,
                                 'seconds': round(time.monotonic() - started, 3), **response})
                print(json.dumps(evidence[-1], ensure_ascii=False), flush=True)
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    Path(args.output).write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
