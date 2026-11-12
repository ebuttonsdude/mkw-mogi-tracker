"""Generated local course pictures appear in reports without bundled assets."""
from io import BytesIO
import unittest
from unittest.mock import patch

from mkw_tracker.reports import render_folder_png
from mkw_tracker.store import TrackerStore
from mkw_tracker.ww_reports import render_ww_folder_png
from mkw_tracker.ww_store import WWStore


class CourseImageReportTests(unittest.TestCase):
    color = (239, 17, 213)

    def picture(self):
        from PIL import Image
        output = BytesIO()
        Image.new('RGBA', (96, 40), (*self.color, 255)).save(output, 'PNG')
        return output.getvalue()

    def assert_picture(self, png, present):
        from PIL import Image
        with Image.open(BytesIO(png)) as image:
            count = sum(pixel == self.color for pixel in image.convert('RGB').getdata())
        self.assertGreater(count, 100) if present else self.assertEqual(count, 0)
        return count

    def test_mogi_and_ffa_course_pictures_keep_names_and_default_layout(self):
        from mkw_tracker.reports import _draw_text
        for mode in ('teams', 'ffa'):
            with self.subTest(mode=mode):
                store = TrackerStore(':memory:', default_format=mode)
                try:
                    ids = [store.add_player(f'Player {index}')['id'] for index in range(12)]
                    store.create_race('one', 'Acorn Heights')
                    store.submit_result('one', ids)
                    snapshot = store.snapshot()
                    folder = snapshot['session']['folder_id']
                    plain = render_folder_png(snapshot, folder)
                    self.assertEqual(plain, render_folder_png(snapshot, folder, course_images={}))
                    self.assert_picture(plain, False)
                    with patch('mkw_tracker.reports._draw_text', wraps=_draw_text) as draw:
                        rendered = render_folder_png(snapshot, folder, course_images={'Acorn Heights': self.picture()})
                    graph_pixels = self.assert_picture(rendered, True)
                    self.assertTrue(any('Acorn Heights' in call.args[2] for call in draw.call_args_list))
                    self.assertEqual(snapshot, store.snapshot())
                    if mode == 'teams':
                        team = store.create_team('Local team')
                        store.update_player(ids[0], 'Player 0', team['id'])
                        highlight = {'highlight_team_id': team['id']}
                    else:
                        highlight = {'highlight_player_id': ids[0]}
                    snapshot = store.snapshot()
                    grid = render_folder_png(snapshot, folder, **highlight,
                                             highlight_session_id=snapshot['session']['id'],
                                             course_images={'Acorn Heights': self.picture()})
                    self.assertGreater(self.assert_picture(grid, True), graph_pixels)
                    self.assertEqual(snapshot, store.snapshot())
                finally:
                    store.close()

    def test_ww_race_and_course_tables_keep_labels_and_mean_position_order(self):
        from mkw_tracker.report_fonts import draw_text
        store = WWStore(':memory:')
        try:
            store.create_race('one', 'Rainbow Road')
            store.set_ww_result('one', position=2, vr_before=8000, vr_after=8010)
            snapshot = store.snapshot()
            folder = snapshot['session']['folder_id']
            plain = render_ww_folder_png(snapshot, folder)
            self.assertEqual(plain, render_ww_folder_png(snapshot, folder, course_images={}))
            self.assert_picture(plain, False)
            with patch('mkw_tracker.ww_reports.draw_text', wraps=draw_text) as draw:
                rendered = render_ww_folder_png(snapshot, folder, course_images={'Rainbow Road': self.picture()})
            self.assert_picture(rendered, True)
            labels = [call.args[2] for call in draw.call_args_list]
            self.assertEqual(sum(label.endswith('Rainbow Road') for label in labels), 2)
            self.assertIn('Mean position', labels)
            self.assertIn('Mean VR change', labels)
            self.assertEqual(snapshot, store.snapshot())
        finally:
            store.close()
