#!/usr/bin/env python3
"""Print WW observations from a read-only local recording; never score a race."""

import argparse
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('video', type=Path)
    parser.add_argument('--stride', type=float, default=.5)
    parser.add_argument('--start', type=float, default=0)
    parser.add_argument('--end', type=float)
    parser.add_argument('--all-observations', action='store_true', help='Include unchanged recognized observations')
    args = parser.parse_args()
    if not args.video.is_file():
        parser.error('video must be an existing local file')
    if (not math.isfinite(args.stride) or args.stride <= 0
            or not math.isfinite(args.start) or args.start < 0
            or (args.end is not None and (not math.isfinite(args.end) or args.end <= args.start))):
        parser.error('stride must be positive and start/end must be finite increasing nonnegative times')

    import cv2
    from mkw_tracker.ww_recognition import WWReader

    video = cv2.VideoCapture(str(args.video))
    reader = None
    try:
        if not video.isOpened():
            raise ValueError('Could not open recording')
        fps = video.get(cv2.CAP_PROP_FPS)
        count = video.get(cv2.CAP_PROP_FRAME_COUNT)
        if not all(math.isfinite(value) and value > 0 for value in (fps, count)):
            raise ValueError('Recording has no usable duration')
        end = min(count / fps, args.end) if args.end is not None else count / fps
        reader = WWReader()
        previous = None
        samples = 0
        while (offset := args.start + samples * args.stride) < end:
            video.set(cv2.CAP_PROP_POS_MSEC, offset * 1000)
            ok, frame = video.read()
            if not ok:
                raise ValueError(f'Could not decode recording at {offset:.3f} seconds')
            course = reader.read_prerace(frame)
            result = reader.read_results(frame) if course is None else None
            observed = {'course': course, 'result': result,
                        'results_layout': reader.is_results_layout(frame) if course is None else False}
            if observed != previous or args.all_observations and (course or result):
                print(json.dumps({'offset_ms': round(offset * 1000), **observed}, ensure_ascii=False), flush=True)
                previous = observed
            samples += 1
        print(json.dumps({'samples': samples, 'start_seconds': args.start, 'end_seconds': end,
                          'stride_seconds': args.stride, 'scores_written': 0}), file=sys.stderr)
    finally:
        video.release()
        if reader is not None:
            reader.close()


if __name__ == '__main__':
    main()
