"""Non-spawning failure-path checks for the documentation screenshot helper."""

from pathlib import Path
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from tools import docs_screenshots


class DocumentationScreenshotTests(unittest.TestCase):
    def test_thread_start_failure_does_not_wait_for_unstarted_server(self):
        playwright = ModuleType('playwright.sync_api')
        playwright.expect = MagicMock()
        playwright.sync_playwright = MagicMock(side_effect=AssertionError('No browser may start'))
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(docs_screenshots, 'ROOT', Path(temporary)), \
                patch.dict('sys.modules', {'playwright.sync_api': playwright}), \
                patch.object(docs_screenshots, 'Application') as application, \
                patch.object(docs_screenshots, 'make_server') as make_server, \
                patch.object(docs_screenshots.threading, 'Thread') as thread:
            thread.return_value.start.side_effect = RuntimeError('thread start failed')
            # Intercept shutdown: a live BaseServer would hang here.
            make_server.return_value.shutdown.side_effect = AssertionError('Unstarted server shutdown')
            with self.assertRaisesRegex(RuntimeError, 'thread start failed'):
                docs_screenshots.main()
            make_server.return_value.shutdown.assert_not_called()
            make_server.return_value.server_close.assert_called_once()
            application.return_value.close.assert_called_once()
            thread.return_value.join.assert_not_called()

    def test_name_review_example_preserves_pending_race_and_distinct_identity(self):
        from mkw_tracker.store import TrackerStore
        with tempfile.TemporaryDirectory() as temporary:
            store = TrackerStore(Path(temporary) / 'example.sqlite')
            try:
                team = store.create_team('Example')
                ids = [store.add_player(f'Example {index}')['id'] for index in range(12)]
                candidate = docs_screenshots.review_example(SimpleNamespace(store=store), ids, team['id'])
                self.assertEqual(candidate['rows'][0]['name'], 'A Apple')
                self.assertEqual(len(candidate['rows']), 12)
                state = store.snapshot()
                self.assertEqual(len(state['players']), 12)
                self.assertEqual(next(player for player in state['players'] if player['id'] == ids[0])['name'], 'AApple')
                self.assertFalse(state['races'][0]['revision'])
                self.assertEqual(state['room']['points'], 0)
            finally:
                store.close()

    def test_ww_example_has_two_sessions_and_combined_course_statistics(self):
        from mkw_tracker.ww_store import WWStore
        with tempfile.TemporaryDirectory() as temporary:
            store = WWStore(Path(temporary) / 'example.sqlite')
            try:
                app = SimpleNamespace(store=store, switch_tracker_mode=MagicMock(), update_settings=MagicMock())
                docs_screenshots.ww_example(app)
                state = store.snapshot()
                self.assertEqual(state['ww']['current_vr'], 8182)
                self.assertEqual(len(state['races']), 12)
                self.assertEqual(len({race['session_id'] for race in state['races']}), 2)
                self.assertEqual(state['ww']['all_courses'][0]['course'], 'Boo Cinema')
                self.assertEqual(state['ww']['all_courses'][0]['average_position'], 2.5)
                self.assertTrue(all(course['races'] == 4 for course in state['ww']['all_courses']))
                self.assertTrue(all(course['races'] == 2 for course in state['ww']['courses']))
            finally:
                store.close()
