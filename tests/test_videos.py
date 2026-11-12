import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mkw_tracker.videos import VideoLibrary


class VideoLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.data = self.base / 'data'
        self.data.mkdir()
        self.uploads = self.data / 'uploads'
        self.app = SimpleNamespace(data_dir=self.data, lock=threading.RLock(), active_uploads=set(),
                                   imports=SimpleNamespace(jobs=[], thread=None))
        self.library = VideoLibrary(self.app)

    def video(self, name='sample.mp4', age=0):
        self.uploads.mkdir(exist_ok=True)
        path = self.uploads / name
        path.write_bytes(b'video')
        modified = time.time() - age * 86400
        os.utime(path, (modified, modified))
        return path

    def job(self, path, status='completed'):
        self.app.imports.jobs.append({'id': str(len(self.app.imports.jobs)), 'path': str(path), 'status': status})

    def test_missing_directory_is_not_created(self):
        self.assertEqual(self.library.snapshot(), {'files': [], 'managed_bytes': 0, 'total_files': 0})
        self.assertFalse(self.uploads.exists())
        self.assertEqual(self.library.cleanup()['deleted_files'], 0)

    def test_lists_only_direct_regular_video_uploads(self):
        managed = self.video('UPPER.MKV')
        self.video('notes.txt')
        outside = self.base / 'original.mp4'
        outside.write_bytes(b'original')
        (self.uploads / 'linked.mp4').symlink_to(outside)
        (self.uploads / 'nested.mp4').mkdir()
        (self.uploads / 'nested.mp4' / 'inside.mp4').write_bytes(b'inner')
        self.job(managed)
        self.job(outside)
        snapshot = self.library.snapshot()
        self.assertEqual(snapshot['total_files'], 1)
        entry = snapshot['files'][0]
        self.assertEqual(entry['name'], 'UPPER.MKV')
        self.assertEqual(entry['import_ids'], ['0'])
        self.assertEqual(entry['size_bytes'], 5)
        self.assertTrue(entry['can_delete'])
        self.assertEqual(len(entry['id']), 64)
        self.assertEqual(self.library.delete(entry['id'])['deleted_bytes'], 5)
        self.assertFalse(managed.exists())
        self.assertTrue(outside.exists())
        self.assertEqual(len(self.app.imports.jobs), 2)

    def test_unknown_ids_cannot_address_originals_or_traverse(self):
        managed = self.video()
        for value in ('sample.mp4', '../sample.mp4', str(managed), None, [], '../../../original.mp4'):
            with self.subTest(value=value), self.assertRaises(KeyError):
                self.library.delete(value)
        self.assertTrue(managed.exists())

    def test_active_jobs_and_uploads_are_protected(self):
        for status in ('queued', 'running', 'cancelling'):
            with self.subTest(status=status):
                path = self.video()
                self.app.imports.jobs = []
                self.job(path, status)
                entry = self.library.snapshot()['files'][0]
                self.assertTrue(entry['active'])
                with self.assertRaisesRegex(ValueError, 'uploading or importing'):
                    self.library.delete(entry['id'])
                self.assertEqual(self.library.cleanup(0)['skipped'], 1)
        self.app.imports.jobs = []
        self.app.active_uploads.add(path.resolve())
        self.assertTrue(self.library.snapshot()['files'][0]['active'])
        self.assertTrue(path.exists())

    def test_reader_still_alive_protects_terminal_job_until_exit(self):
        path = self.video()
        self.job(path, 'cancelled')
        self.app.imports.thread = Mock(is_alive=Mock(return_value=True))
        entry = self.library.snapshot()['files'][0]
        self.assertFalse(entry['can_delete'])
        self.app.imports.thread.is_alive.return_value = False
        self.assertTrue(self.library.snapshot()['files'][0]['can_delete'])

    def test_failed_cleanup_protects_terminal_import_file(self):
        path = self.video()
        self.job(path, 'failed')
        self.app.imports.cleanup_failed = True
        entry = self.library.snapshot()['files'][0]
        self.assertFalse(entry['can_delete'])
        with self.assertRaisesRegex(ValueError, 'uploading or importing'):
            self.library.delete(entry['id'])

    def test_bulk_age_skips_recent_and_active_and_keeps_original(self):
        old = self.video('old.mp4', age=40)
        active = self.video('active.mp4', age=40)
        recent = self.video('recent.mp4', age=2)
        outside = self.base / 'outside.mp4'
        outside.write_bytes(b'original')
        self.job(outside)
        self.job(active, 'running')
        result = self.library.cleanup()
        self.assertEqual(result, {'deleted_files': 1, 'deleted_bytes': 5, 'skipped': 2, 'errors': []})
        self.assertFalse(old.exists())
        self.assertTrue(outside.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(active.exists())
        self.assertEqual(self.library.cleanup(0)['deleted_files'], 1)

    def test_age_validation(self):
        for value in (-1, 36501, True, False, .5, float('nan'), float('inf'), '30', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.library.cleanup(value)

    def test_symlink_upload_root_refused(self):
        external = self.base / 'external'
        external.mkdir()
        original = external / 'original.mp4'
        original.write_bytes(b'original')
        self.uploads.symlink_to(external, target_is_directory=True)
        for action in (self.library.snapshot, self.library.cleanup, lambda: self.library.delete('id')):
            with self.assertRaises(ValueError):
                action()
        self.assertTrue(original.exists())

    def test_bulk_permission_failure_reports_partial_success(self):
        self.video('allowed.mp4', age=40)
        denied = self.video('denied.mp4', age=40)
        unlink = self.library._unlink

        def controlled(descriptor, entry, expected):
            if entry['name'] == denied.name:
                raise PermissionError('denied fixture')
            return unlink(descriptor, entry, expected)

        with patch.object(self.library, '_unlink', side_effect=controlled):
            result = self.library.cleanup()
        self.assertEqual(result['deleted_files'], 1)
        self.assertEqual(result['deleted_bytes'], 5)
        self.assertEqual(result['errors'][0]['name'], denied.name)
        self.assertTrue(denied.exists())

    def test_replaced_entry_is_not_deleted(self):
        path = self.video()
        original_unlink = self.library._unlink

        def replaced(descriptor, entry, expected):
            path.unlink()
            path.symlink_to(self.base / 'original.mp4')
            original_unlink(descriptor, entry, expected)

        entry = self.library.snapshot()['files'][0]
        with patch.object(self.library, '_unlink', side_effect=replaced):
            with self.assertRaisesRegex(ValueError, 'changed'):
                self.library.delete(entry['id'])
        self.assertTrue(path.is_symlink())

    def test_file_changed_during_delete_is_protected(self):
        path = self.video()
        original_unlink = self.library._unlink

        def changed(descriptor, entry, expected):
            path.write_bytes(b'new uploaded contents')
            original_unlink(descriptor, entry, expected)

        entry = self.library.snapshot()['files'][0]
        with patch.object(self.library, '_unlink', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'changed'):
                self.library.delete(entry['id'])
        self.assertEqual(path.read_bytes(), b'new uploaded contents')

    def test_hardlink_removes_only_managed_directory_entry(self):
        self.uploads.mkdir()
        original = self.base / 'original.mp4'
        original.write_bytes(b'original')
        os.link(original, self.uploads / 'managed.mp4')
        entry = self.library.snapshot()['files'][0]
        self.library.delete(entry['id'])
        self.assertEqual(original.read_bytes(), b'original')
        self.assertFalse((self.uploads / 'managed.mp4').exists())

    def test_exchanged_root_does_not_delete_external_file(self):
        self.video()
        external = self.base / 'external'
        external.mkdir()
        original = external / 'sample.mp4'
        original.write_bytes(b'external')
        original_unlink = self.library._unlink

        def exchanged(descriptor, entry, expected):
            self.uploads.rename(self.data / 'saved-uploads')
            self.uploads.symlink_to(external, target_is_directory=True)
            original_unlink(descriptor, entry, expected)

        entry = self.library.snapshot()['files'][0]
        with patch.object(self.library, '_unlink', side_effect=exchanged):
            if os.name == 'nt':
                with self.assertRaises(PermissionError):
                    self.library.delete(entry['id'])
            else:
                with self.assertRaisesRegex(ValueError, 'directory changed'):
                    self.library.delete(entry['id'])
        self.assertEqual(original.read_bytes(), b'external')
        preserved = self.uploads if os.name == 'nt' else self.data / 'saved-uploads'
        self.assertTrue((preserved / 'sample.mp4').exists())


if __name__ == '__main__':
    unittest.main()
