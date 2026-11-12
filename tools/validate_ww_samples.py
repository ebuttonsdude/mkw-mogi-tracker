#!/usr/bin/env python3
"""Validate WW sessions against optional, read-only issue #20 sample recordings.

This is a full CPU replay, not a unit test. It can take several minutes per file.
No OBS connection, camera, name model or real tracker database is opened.
"""

import argparse
from contextlib import contextmanager, ExitStack
from fractions import Fraction
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REFERENCE = ROOT / 'tests' / 'fixtures' / 'ww' / 'sessions.json'


def probe_video(path, ffprobe):
    """Reject changed geometry/rate rather than attaching incorrect timestamps."""
    result = subprocess.run(
        [ffprobe, '-v', 'error', '-select_streams', 'v:0', '-show_entries',
         'stream=width,height,r_frame_rate,avg_frame_rate:format=duration', '-of', 'json', str(path)],
        capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise ValueError(f'ffprobe could not read {path.name}: {result.stderr.strip()[:500]}')
    try:
        metadata = json.loads(result.stdout)
        stream = metadata['streams'][0]
        size = stream['width'], stream['height']
        rates = Fraction(stream['r_frame_rate']), Fraction(stream['avg_frame_rate'])
        duration = float(metadata['format']['duration'])
    except (ValueError, KeyError, IndexError, TypeError, ZeroDivisionError) as error:
        raise ValueError(f'{path.name} has unusable video metadata') from error
    if size != (2560, 1440) or rates != (Fraction(60), Fraction(60)) or not 0 < duration < 3600:
        raise ValueError(f'{path.name}: expected supplied 2560x1440, 60 FPS recording under one hour; '
                         f'got {size}, rates {rates}, duration {duration}')
    return size, duration


@contextmanager
def workspace(output_dir, sample):
    if output_dir is None:
        with tempfile.TemporaryDirectory(prefix=f'ww-validation-{sample}-') as directory:
            yield Path(directory)
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        yield Path(tempfile.mkdtemp(prefix=sample + '-', dir=output_dir))


@contextmanager
def decoder(ffmpeg, video, log_path):
    """Keep decoder ownership bounded, including interruption and OCR failures."""
    with log_path.open('w', encoding='utf-8') as log:
        process = subprocess.Popen(
            [ffmpeg, '-nostdin', '-v', 'warning', '-threads', '4', '-i', str(video),
             '-map', '0:v:0', '-an', '-sn', '-filter_threads', '1',
             '-vf', r'select=not(mod(n\,30))', '-fps_mode', 'passthrough',
             '-pix_fmt', 'bgr24', '-f', 'rawvideo', 'pipe:1'],
            stdout=subprocess.PIPE, stderr=log)
        try:
            yield process
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            process.stdout.close()


def validate_session(snapshot, reference):
    """Compare manually checked facts; timestamps are checked only structurally."""
    races = snapshot['races']
    expected = reference['races']
    failures = []
    if len(races) != len(expected):
        failures.append(f'Expected {len(expected)} races, got {len(races)}')
    offsets = []
    for index, race in enumerate(races):
        if race['course'].startswith('Unknown course'):
            failures.append(f'Race {index + 1}: unexpected unknown-course race')
        offset = race.get('offset_ms')
        if type(offset) is not int or offset < 0:
            failures.append(f'Race {index + 1}: missing or invalid intro timestamp')
        else:
            offsets.append(offset)
    if any(later <= earlier for earlier, later in zip(offsets, offsets[1:])):
        failures.append('Intro timestamps are not strictly increasing')
    for index, (race, wanted) in enumerate(zip(races, expected), 1):
        result = race.get('ww_result')
        actual = (race['course'], result['position'] if result else None,
                  result['vr_after'] if result else None)
        target = wanted['course'], wanted['position'], wanted['vr_after']
        if actual != target:
            failures.append(f'Race {index}: expected {target!r}, got {actual!r}')
        # A missing delta is allowed. Any retained change must be self-consistent.
        if result and result['delta'] is not None:
            if result['vr_before'] is None or result['vr_before'] + result['delta'] != result['vr_after']:
                failures.append(f'Race {index}: inconsistent known VR change')
    pending = sum(race.get('ww_result') is None for race in races)
    if pending != reference['expected_pending']:
        failures.append(f"Expected {reference['expected_pending']} pending races, got {pending}")
    return failures


def replay(sample, reference, video, ffmpeg, ffprobe, output_dir, tessdata):
    size, duration = probe_video(video, ffprobe)
    # Existing capture dependencies only; no optional name readers or GPU work.
    import cv2
    import numpy as np
    from mkw_tracker.ww_capture import WWCaptureCoordinator
    from mkw_tracker.ww_recognition import WWReader
    from mkw_tracker.ww_store import WWStore

    cv2.setNumThreads(1)
    os.environ['OMP_THREAD_LIMIT'] = '1'
    started = time.monotonic()
    with workspace(output_dir, sample) as directory, ExitStack() as cleanup:
        (directory / 'ww').mkdir()
        store = WWStore(directory / 'ww' / 'tracker.sqlite3')
        cleanup.callback(store.close)
        reader = WWReader(tessdata_path=tessdata)
        cleanup.callback(reader.close)
        session = store.snapshot()['session']
        capture = WWCaptureCoordinator(store, threading.RLock(), directory / 'ww', reader,
                                       session_id=session['id'], folder_id=session['folder_id'])
        recording = store.create_recording(video.name)['id']
        count = 0
        frame_bytes = size[0] * size[1] * 3
        log_path = directory / 'decode.log'
        with decoder(ffmpeg, video, log_path) as process:
            while True:
                payload = process.stdout.read(frame_bytes)
                if not payload:
                    break
                if len(payload) != frame_bytes:
                    raise RuntimeError(f'{video.name}: decoder returned a truncated frame')
                frame = np.frombuffer(payload, dtype=np.uint8).reshape(size[1], size[0], 3)
                capture.observe(frame, recording, count * 500)
                count += 1
                if count % 120 == 0:
                    print(json.dumps({'sample': sample, 'progress_seconds': count / 2,
                                      'duration_seconds': duration}), flush=True)
            returncode = process.wait(timeout=10)
            if returncode:
                detail = log_path.read_text(encoding='utf-8', errors='replace')[-1000:]
                raise RuntimeError(f'{video.name}: ffmpeg exited {returncode}: {detail}')
        if count == 0 or abs(count / 2 - duration) > 1:
            raise RuntimeError(f'{video.name}: incomplete decode, {count} samples for {duration:.3f} seconds')
        snapshot = store.snapshot()
        failures = validate_session(snapshot, reference)
        report = {'sample': sample, 'passed': not failures, 'failures': failures, 'samples': count,
                  'wall_seconds': round(time.monotonic() - started, 1),
                  'races': [{'course': race['course'], 'offset_ms': race['offset_ms'],
                             'result': race['ww_result']} for race in snapshot['races']]}
        if output_dir is not None:
            report['output_dir'] = str(directory)
            (directory / 'snapshot.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
            (directory / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return not failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sample', choices=('hardname', 'easyname'), nargs='?', help='Default: both samples')
    parser.add_argument('--sample-dir', type=Path, default=ROOT / 'video-samples')
    parser.add_argument('--output-dir', type=Path, help='Keep isolated DB/evidence/logs here; otherwise remove all outputs')
    parser.add_argument('--tessdata', type=Path, default=ROOT / '.local' / 'tessdata')
    args = parser.parse_args(argv)
    reference = json.loads(REFERENCE.read_text(encoding='utf-8'))['samples']
    samples = [args.sample] if args.sample else list(reference)
    files = {sample: args.sample_dir / reference[sample]['filename'] for sample in samples}
    missing = [str(path) for path in files.values() if not path.is_file()]
    if missing:
        parser.error('Optional original recordings are missing; nothing was decoded: ' + ', '.join(missing))
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        parser.error('ffmpeg and ffprobe must be installed for optional full-sample validation')
    try:
        passed = True
        for sample in samples:
            passed = replay(sample, reference[sample], files[sample], ffmpeg, ffprobe,
                            args.output_dir, args.tessdata) and passed
        return 0 if passed else 1
    except KeyboardInterrupt:
        print('Validation interrupted; decoder stopped and temporary outputs removed.', file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, ImportError, subprocess.TimeoutExpired) as error:
        print(f'WW sample validation failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
