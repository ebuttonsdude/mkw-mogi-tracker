#!/usr/bin/env python3
"""Replay a local recording and emit OCR observations. Never commits scores."""
import argparse
import json
import math
import sys
import threading
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mkw_tracker.recognition import ResultReader
from mkw_tracker.neural_ocr import make_name_reader
from mkw_tracker.name_engines import NAME_ENGINES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('video', type=Path)
    parser.add_argument('--stride', type=float, default=.5, help='Seconds between sampled frames (default 0.5); shorter intervals help capture brief results windows')
    parser.add_argument('--start', type=float, default=0)
    parser.add_argument('--end', type=float)
    parser.add_argument('--tessdata', default='.local/tessdata')
    # Standalone replay has no private endpoint/consent configuration surface.
    parser.add_argument('--name-engine', choices=sorted(NAME_ENGINES - {'external-vision'}), default='auto')
    parser.add_argument('--data-dir', type=Path, help='Save review queue and evidence in a fresh directory')
    args = parser.parse_args()
    if not all(math.isfinite(value) for value in (args.stride, args.start)
               if value is not None) or (args.end is not None and not math.isfinite(args.end)):
        parser.error('start, end, and stride must be finite numbers')
    if args.stride <= 0 or args.start < 0:
        parser.error('stride must be positive and start must be nonnegative')
    if args.end is not None and args.end < args.start:
        parser.error('end must be greater than or equal to start')
    if args.data_dir and args.data_dir.exists():
        if not args.data_dir.is_dir() or any(args.data_dir.iterdir()):
            parser.error('--data-dir must be a new or empty directory; choose another path to preserve saved reviews')
    capture = cv2.VideoCapture(str(args.video))
    if not capture.isOpened():
        parser.error(f'Cannot open {args.video}')
    reader = ResultReader(args.tessdata, name_reader=make_name_reader(args.name_engine))
    offset = args.start
    store = coordinator = None
    try:
        if args.data_dir:
            from mkw_tracker.capture import CaptureCoordinator
            from mkw_tracker.store import TrackerStore

            args.data_dir.mkdir(parents=True, exist_ok=True)
            store = TrackerStore(args.data_dir / 'tracker.sqlite3')
            coordinator = CaptureCoordinator(store, threading.RLock(), args.data_dir, reader)
            recording_id = store.create_recording(f'Video replay: {args.video.name}')['id']
        while args.end is None or offset <= args.end:
            capture.set(cv2.CAP_PROP_POS_MSEC, offset * 1000)
            ok, frame = capture.read()
            if not ok:
                break
            if coordinator:
                coordinator.observe(frame, recording_id, int(offset * 1000))
            else:
                result = reader.read(frame)
                prerace = reader.read_prerace(frame)
                if result['phase'] != 'unknown' or prerace:
                    print(json.dumps({'offset_seconds': offset, 'results': result,
                                      'prerace': prerace}, ensure_ascii=False), flush=True)
            offset += args.stride
        if coordinator:
            races = store.snapshot()['races']
            print(json.dumps({'data_dir': str(args.data_dir), 'races': len(races),
                              'pending': len(coordinator.snapshot()['pending']),
                              'scored': sum(race['status'] == 'scored' for race in races),
                              'chapters': [{'course': race['course'], 'offset_ms': race['offset_ms']}
                                           for race in races]}, ensure_ascii=False), flush=True)
    finally:
        capture.release()
        try:
            if coordinator:
                coordinator.close()
            else:
                reader.close()
        finally:
            if store:
                store.close()


if __name__ == '__main__':
    main()
