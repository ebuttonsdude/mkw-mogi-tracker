"""Commit-driven OBS output, including background WW writes and retry shutdown."""

from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from mkw_tracker.server import Application


class OutputSyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = Application(':memory:', self.directory.name)
        self.addCleanup(self.app.close)

    def test_committed_store_writes_update_output_without_polling(self):
        with self.app.lock:
            team = self.app.store.create_team('Committed team')
            self.assertIn('Committed team', self.app.obs_text_path.read_text())
            with self.assertRaises(RuntimeError):
                with self.app.store._transaction():
                    self.app.store._write('UPDATE teams SET name=? WHERE id=?', ('Rolled back', team['id']))
                    raise RuntimeError('Abort')
            self.assertNotIn('Rolled back', self.app.obs_text_path.read_text())
        with patch.object(self.app, 'sync_outputs', side_effect=AssertionError('GET output write')):
            self.app.snapshot()

    def test_ww_automatic_results_and_corrections_update_without_browser(self):
        self.app.switch_tracker_mode('ww', self.app.store.active_session()['id'])
        with self.app.lock:
            store = self.app.store
            store.create_race('automatic', 'Rainbow Road')
            store.set_ww_result('automatic', position=1, vr_after=8100, source='capture')
            self.assertIn('Current VR: 8100', self.app.obs_text_path.read_text())
            store.set_ww_result('automatic', position=1, vr_after=8200)
            self.assertIn('Current VR: 8200', self.app.obs_text_path.read_text())
            store.delete_race('automatic')
            self.assertIn('Current VR: Unknown', self.app.obs_text_path.read_text())

    def test_failed_output_retries_without_poll_and_keeps_committed_data(self):
        initial = self.app.obs_text_path.read_text()
        error = OSError('Temporary output failure')
        with patch.object(Path, 'replace', side_effect=error), \
                patch('mkw_tracker.server.log_event') as report:
            with self.app.lock:
                self.app.store.create_team('Saved despite output failure')
                self.assertIsNotNone(self.app.obs_text_error)
                self.assertEqual(self.app.obs_text_path.read_text(), initial)
                self.assertEqual(self.app.store.get_team(self.app.store.snapshot()['teams'][0]['id'])['name'],
                                 'Saved despite output failure')
            self.app.sync_outputs()  # Repeated retries should not flood stdout.
            report.assert_called_once_with('app', 'output_failed', error=error)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            with self.app.lock:
                if self.app.obs_text_error is None:
                    break
            time.sleep(.02)
        self.assertIsNone(self.app.obs_text_error)
        self.assertIn('Saved despite output failure', self.app.obs_text_path.read_text())

    def test_close_cancels_output_retry_before_database_close(self):
        with patch.object(Path, 'replace', side_effect=OSError('Output failure')):
            with self.app.lock:
                self.app.store.create_team('Saved')
                retry = self.app._output_retry
                self.assertIsNotNone(retry)
            self.app.close()
        self.assertFalse(retry.is_alive())
        # Cleanup must not close the same sqlite connection again through snapshots.
        self.app.close()

    def test_output_database_failure_is_logged_without_undoing_committed_write(self):
        error = sqlite3.OperationalError('Output query failed')
        with patch.object(self.app, '_session_snapshot', side_effect=error), \
                patch('mkw_tracker.server.log_event') as report:
            with self.app.lock:
                team = self.app.store.create_team('Already committed')
            report.assert_called_once_with('app', 'output_failed', error=error)
        self.assertEqual(self.app.store.get_team(team['id'])['name'], 'Already committed')
        self.assertIn('data', self.app.obs_text_error)
        self.assertIsNone(self.app._output_retry)
        self.app.sync_outputs()
        self.assertIsNone(self.app.obs_text_error)


if __name__ == '__main__':
    unittest.main()
