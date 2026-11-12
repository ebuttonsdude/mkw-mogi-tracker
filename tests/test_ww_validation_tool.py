"""Fast guards for the optional full-recording validator; no video is decoded."""

from copy import deepcopy
from contextlib import nullcontext
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import validate_ww_samples as validator


class WWValidationToolTests(unittest.TestCase):
    def reference_snapshot(self):
        reference = json.loads(validator.REFERENCE.read_text())['samples']['hardname']
        races = []
        for index, expected in enumerate(reference['races']):
            result = ({'position': expected['position'], 'vr_after': expected['vr_after'],
                       'vr_before': None, 'delta': None} if expected['position'] is not None else None)
            races.append({'course': expected['course'], 'offset_ms': index * 1000, 'ww_result': result})
        return reference, {'races': races}

    def test_reference_accepts_unknown_changes_and_one_pending_final_course(self):
        reference, snapshot = self.reference_snapshot()
        self.assertEqual(validator.validate_session(snapshot, reference), [])

    def test_duplicate_or_unknown_course_races_fail(self):
        reference, snapshot = self.reference_snapshot()
        extra = deepcopy(snapshot['races'][0])
        extra.update(course='Unknown course (capture started mid-race)', offset_ms=100000)
        snapshot['races'].append(extra)
        failures = validator.validate_session(snapshot, reference)
        self.assertTrue(any('Expected 8 races' in error for error in failures))
        self.assertTrue(any('unknown-course' in error for error in failures))

    def test_wrong_final_rating_and_unordered_timestamps_fail(self):
        reference, snapshot = self.reference_snapshot()
        snapshot['races'][0]['ww_result']['vr_after'] = 1
        snapshot['races'][1]['offset_ms'] = 0
        failures = validator.validate_session(snapshot, reference)
        self.assertTrue(any('Race 1:' in error for error in failures))
        self.assertTrue(any('increasing' in error for error in failures))

    def test_missing_originals_fail_before_decoder_or_reader(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(validator, 'replay') as replay, patch('sys.stderr', new_callable=io.StringIO) as stderr:
            with self.assertRaises(SystemExit) as raised:
                validator.main(['--sample-dir', directory])
            self.assertEqual(raised.exception.code, 2)
            self.assertIn('Optional original recordings are missing', stderr.getvalue())
            replay.assert_not_called()

    def test_probe_rejects_different_frame_rate(self):
        metadata = {'streams': [{'width': 2560, 'height': 1440, 'r_frame_rate': '30/1',
                                  'avg_frame_rate': '30/1'}], 'format': {'duration': '100'}}
        result = Mock(returncode=0, stdout=json.dumps(metadata))
        with patch.object(validator.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(ValueError, '60 FPS'):
                validator.probe_video(Path('sample.mkv'), 'ffprobe')

    def test_interrupted_decoder_is_terminated_then_killed_if_needed(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('ffmpeg', 5), 0]
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(validator.subprocess, 'Popen', return_value=process):
            with self.assertRaises(KeyboardInterrupt):
                with validator.decoder('ffmpeg', Path('sample.mkv'), Path(directory) / 'decode.log'):
                    raise KeyboardInterrupt
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        process.stdout.close.assert_called_once()

    def test_default_workspace_is_removed_on_failure(self):
        output = None
        with self.assertRaises(RuntimeError):
            with validator.workspace(None, 'hardname') as directory:
                output = directory
                (directory / 'evidence.png').write_bytes(b'fixture')
                raise RuntimeError('recognition failed')
        self.assertFalse(output.exists())

    @unittest.skipUnless(importlib.util.find_spec('cv2') and importlib.util.find_spec('numpy'),
                         'optional capture dependencies are not installed')
    def test_replay_uses_import_owner_not_live_continuity(self):
        process = Mock(stdout=io.BytesIO(b'\x00\x00\x00'))
        process.wait.return_value = 0
        reference = {'races': [], 'expected_pending': 0}
        with patch.object(validator, 'probe_video', return_value=((1, 1), .5)), \
                patch.object(validator, 'decoder', return_value=nullcontext(process)), \
                patch('mkw_tracker.ww_recognition.WWReader'), \
                patch('mkw_tracker.ww_capture.WWCaptureCoordinator') as coordinator, \
                patch('sys.stdout', new_callable=io.StringIO):
            self.assertTrue(validator.replay('sample', reference, Path('sample.mkv'),
                                            'ffmpeg', 'ffprobe', None, Path('unused-tessdata')))
        kwargs = coordinator.call_args.kwargs
        self.assertTrue(kwargs['session_id'])
        self.assertTrue(kwargs['folder_id'])
        coordinator.return_value.observe.assert_called_once()


if __name__ == '__main__':
    unittest.main()
