"""Pure filesystem tests; wiki synchronization never launches Git or subprocesses."""

import json
from pathlib import Path
import tempfile
import unittest

from tools import build_wiki, sync_wiki


class WikiSyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'export'
        self.wiki = self.root / 'wiki'
        self.wiki.mkdir()
        self.export({'Home.md': '# Home\n', '_Sidebar.md': 'Navigation\n', 'guide.md': '# Guide\n'})

    def export(self, pages):
        build_wiki.publish_output(pages, self.source)

    def sync(self):
        return sync_wiki.sync_wiki(self.source, self.wiki)

    def test_initial_home_replaced_unrelated_content_and_git_preserved(self):
        (self.wiki / 'Home.md').write_text('Welcome to the wiki!')
        (self.wiki / 'Manual-notes.md').write_text('User notes')
        git = self.wiki / '.git'
        git.mkdir()
        (git / 'config').write_text('private configuration')
        changed, removed = self.sync()
        self.assertEqual(changed, ['Home.md', '_Sidebar.md', 'guide.md'])
        self.assertEqual(removed, [])
        self.assertEqual((self.wiki / 'Home.md').read_text(), '# Home\n')
        self.assertEqual((self.wiki / 'Manual-notes.md').read_text(), 'User notes')
        self.assertEqual((git / 'config').read_text(), 'private configuration')
        self.assertFalse((self.wiki / build_wiki.OUTPUT_MARKER).exists())
        self.assertEqual(set(json.loads((self.wiki / sync_wiki.MANIFEST).read_text())['files']), set(changed))

    def test_update_and_stale_removal_only_touch_owned_pages(self):
        self.sync()
        (self.wiki / 'other.md').write_text('Keep me')
        self.export({'Home.md': '# Updated\n', '_Sidebar.md': 'Navigation\n', 'new-page.md': 'New\n'})
        changed, removed = self.sync()
        self.assertEqual(changed, ['Home.md', 'new-page.md'])
        self.assertEqual(removed, ['guide.md'])
        self.assertFalse((self.wiki / 'guide.md').exists())
        self.assertEqual((self.wiki / 'other.md').read_text(), 'Keep me')
        self.assertEqual(self.sync(), ([], []))

    def test_unowned_conflict_fails_before_any_changes(self):
        (self.wiki / 'guide.md').write_text('My own guide')
        (self.wiki / 'Home.md').write_text('Original home')
        with self.assertRaisesRegex(ValueError, 'unowned wiki page'):
            self.sync()
        self.assertEqual((self.wiki / 'Home.md').read_text(), 'Original home')
        self.assertFalse((self.wiki / sync_wiki.MANIFEST).exists())

    def test_manual_edits_to_generated_page_are_not_silently_deleted(self):
        self.sync()
        (self.wiki / 'guide.md').write_text('Manual additions')
        self.export({'Home.md': '# Changed', '_Sidebar.md': 'Changed'})
        with self.assertRaisesRegex(ValueError, 'was edited'):
            self.sync()
        self.assertEqual((self.wiki / 'guide.md').read_text(), 'Manual additions')
        self.assertEqual((self.wiki / 'Home.md').read_text(), '# Home\n')

    def test_invalid_manifest_paths_and_hashes_are_rejected(self):
        for name, checksum in (('../outside.md', 'a' * 64), ('.git/config', 'b' * 64),
                               ('_Sidebar.md', 'not-a-hash'), ('/tmp/page.md', 'c' * 64)):
            with self.subTest(name=name):
                (self.wiki / sync_wiki.MANIFEST).write_text(json.dumps({'schema_version': 1, 'files': {name: checksum}}))
                with self.assertRaisesRegex(ValueError, 'Unsafe filename or checksum'):
                    self.sync()
                self.assertFalse((self.wiki / 'Home.md').exists())

    def test_invalid_manifest_structure_is_rejected(self):
        for manifest in ({}, [], {'schema_version': 2, 'files': {}},
                         {'schema_version': True, 'files': {'Home.md': 'a' * 64}},
                         {'schema_version': 1, 'files': []}, {'schema_version': 1, 'files': {}}):
            with self.subTest(manifest=manifest):
                (self.wiki / sync_wiki.MANIFEST).write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    self.sync()

    def test_symlink_destinations_sources_manifests_and_pages_rejected(self):
        link = self.root / 'link'
        link.symlink_to(self.wiki, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'directories'):
            sync_wiki.sync_wiki(self.source, link)
        outside = self.root / 'outside.md'
        outside.write_text('Untouched')
        (self.wiki / 'Home.md').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.sync()
        self.assertEqual(outside.read_text(), 'Untouched')
        (self.wiki / 'Home.md').unlink()
        (self.wiki / sync_wiki.MANIFEST).symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.sync()
        (self.wiki / sync_wiki.MANIFEST).unlink()
        (self.source / 'guide.md').unlink()
        (self.source / 'guide.md').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.sync()

    def test_symlink_owned_page_cannot_be_removed(self):
        self.sync()
        page = self.wiki / 'guide.md'
        page.unlink()
        outside = self.root / 'outside.md'
        outside.write_text('# Guide\n')
        page.symlink_to(outside)
        self.export({'Home.md': '# Home\n', '_Sidebar.md': 'Navigation\n'})
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.sync()
        self.assertTrue(page.is_symlink())
        self.assertEqual(outside.read_text(), '# Guide\n')

    def test_source_requires_owned_complete_export(self):
        (self.source / build_wiki.OUTPUT_MARKER).write_text('wrong')
        with self.assertRaisesRegex(ValueError, 'owned wiki export'):
            self.sync()
        (self.source / build_wiki.OUTPUT_MARKER).write_text(build_wiki.MARKER_CONTENT)
        self.export({'Home.md': '# Home'})
        with self.assertRaisesRegex(ValueError, 'requires Home.md and _Sidebar.md'):
            self.sync()
        self.export({'Home.md': '# Home', '_Sidebar.md': 'Nav'})
        (self.source / 'private.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Unexpected generated'):
            self.sync()
        with self.assertRaisesRegex(ValueError, 'separately'):
            sync_wiki.sync_wiki(self.source, self.source)

    def test_symlink_parent_is_rejected(self):
        parent_link = self.root / 'linked-root'
        parent_link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            sync_wiki.sync_wiki(self.source, parent_link / 'wiki')
        self.assertFalse((self.wiki / 'Home.md').exists())

    def test_missing_previously_owned_page_can_be_restored_or_retired(self):
        self.sync()
        (self.wiki / 'guide.md').unlink()
        self.assertEqual(self.sync(), (['guide.md'], []))
        (self.wiki / 'guide.md').unlink()
        self.export({'Home.md': '# Home\n', '_Sidebar.md': 'Navigation\n'})
        self.assertEqual(self.sync(), ([], []))


if __name__ == '__main__':
    unittest.main()
