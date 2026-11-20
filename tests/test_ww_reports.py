from copy import deepcopy
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mkw_tracker.ww_reports import (MAX_REPORT_HEIGHT, MAX_REPORT_RACES, REPORT_WIDTH,
                                    render_ww_folder_png, vr_graph_segments, vr_scaled_bounds,
                                    ww_folder_report)
from mkw_tracker.ww_store import WWStore


class WWReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = WWStore(Path(self.temp.name) / "ww.sqlite")
        self.session = self.store.snapshot()["session"]
        self.folder = self.session["folder_id"]

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def race(self, identifier, course="Rainbow Road", **result):
        self.store.create_race(identifier, course)
        if result:
            self.store.set_ww_result(identifier, **result)

    def test_reordered_report_graph_and_recent_window_match_live_and_import_policy(self):
        ids = [f'r{i}' for i in range(12)]
        for i, race_id in enumerate(ids):
            live = i != 1
            self.race(race_id, race_id, position=1, vr_after=9000 + i,
                      source='capture' if live else 'import', derive_from_previous=live)
        self.assertEqual(self.store.snapshot()["ww"]["last_10_change"], 10)
        # Move the imported observation and original first finish to the end.
        ordered = ['r2', *ids[3:], 'r1', 'r0']
        self.store.reorder_history(self.folder, ordered, ids)
        snapshot = self.store.snapshot()
        report = ww_folder_report(snapshot, self.folder)
        self.assertEqual([r['id'] for r in report['races']], ordered)
        self.assertEqual([p['race_id'] for p in report['graph']], ordered)
        self.assertEqual(report['graph'], snapshot['ww']['graph'])
        self.assertEqual(report['current_vr'], 9000)
        self.assertIsNone(report['starting_vr'])
        self.assertIsNone(report['last_10_change'])  # import has no observed delta
        for race in report['races']:
            self.assertEqual(race['ww_result'], self.store._race(race['id'])['ww_result'])
            self.assertEqual(race['revision'], 1)
        self.assertEqual(report['races'][-1]['ww_result']['delta'], -1)
        self.assertIsNone(report['races'][-2]['ww_result']['delta'])

    def image(self, snapshot=None):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is not installed")
        try:
            content = render_ww_folder_png(snapshot or self.store.snapshot(), self.folder)
        except RuntimeError as error:
            if "Japanese-capable font" in str(error):
                self.skipTest(str(error))
            raise
        self.assertTrue(content.startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(BytesIO(content)) as image:
            image.load()
            self.assertEqual(image.width, REPORT_WIDTH)
            self.assertLessEqual(image.height, MAX_REPORT_HEIGHT)
            self.assertGreater(image.height, 700)
            return image.copy()

    def test_report_for_inactive_folder_uses_own_player_and_ratings(self):
        self.store.set_player_name("Old 日本語☆", self.session["id"])
        self.race("old", position=5, vr_before=8576, vr_after=8628)
        report = ww_folder_report(self.store.snapshot(), self.folder)
        current = self.store.create_session("Another WW", self.session["id"])
        self.store.set_player_name("New player", current["id"])
        self.race("new", position=24, vr_after=4000)
        inactive = ww_folder_report(self.store.snapshot(), self.folder)
        self.assertEqual(inactive, report)
        self.assertEqual(inactive["player_name"], "Old 日本語☆")
        self.assertEqual(inactive["current_vr"], 8628)
        self.assertEqual(inactive["peak_vr"], 8628)
        self.assertEqual(inactive["lowest_vr"], 8576)
        self.assertEqual([race["id"] for race in inactive["races"]], ["old"])

    def test_report_uses_latest_correction_and_matches_shared_summary(self):
        self.race("a", position=1, vr_before=8000, vr_after=8050)
        self.race("b", position=2, vr_before=8050, vr_after=8070)
        self.store.set_ww_result("a", position=8, vr_before=8000, vr_after=8040)
        state = self.store.snapshot()
        report = ww_folder_report(state, self.folder)
        for key, value in state["ww"].items():
            if key != "all_courses":
                self.assertEqual(report[key], value, key)
        self.assertEqual(report["known_change_sum"], 60)
        self.assertEqual(report["races"][0]["ww_result"]["position"], 8)
        self.assertEqual(report["courses"][0]["average_position"], 5)

    def test_unknowns_and_discontinuities_break_graph_and_change_denominator(self):
        self.race("a", position=1, vr_before=8000, vr_after=8050)
        self.race("cut", position=8, vr_after=8100)
        self.race("pending")
        self.race("d", position=2, vr_before=8200, vr_after=8210)
        report = ww_folder_report(self.store.snapshot(), self.folder)
        self.assertEqual(vr_graph_segments(report["graph"]),
                         [[(0, 8000), (1, 8050)], [(2, 8100)], [(4, 8210)]])
        self.assertEqual(report["known_changes"], 2)
        self.assertEqual(report["recorded_races"], 3)
        self.assertEqual(report["pending_races"], 1)
        self.assertEqual(report["courses"][0]["known_changes"], 2)
        self.assertEqual(report["courses"][0]["races"], 3)
        self.assertEqual(report["courses"][0]["average_change"], 30)
        self.assertIsNone(report["total_change"])
        self.assertIsNone(report["races"][1]["ww_result"]["delta"])

    def test_empty_report_is_not_mogi_scoring(self):
        report = ww_folder_report(self.store.snapshot(), self.folder)
        self.assertEqual(report["races"], [])
        self.assertEqual(report["courses"], [])
        self.assertIsNone(report["current_vr"])
        self.assertEqual(report["player_name"], "Local player")
        for key in ("points", "maximum_points", "teams", "players", "expected_races"):
            self.assertNotIn(key, report)
        self.assertEqual(vr_graph_segments(report["graph"]), [])
        self.image()

    def test_vr_bounds_are_local_to_ratings_and_contain_all_values(self):
        lower, upper, ticks = vr_scaled_bounds([None, 8736, 8759, 8805])
        self.assertGreater(lower, 8000)
        self.assertLessEqual(lower, 8736)
        self.assertGreaterEqual(upper, 8805)
        self.assertEqual(ticks[0], lower)
        self.assertEqual(ticks[-1], upper)
        for values in ([9000], [1], [99999], [1, 99999], [None], []):
            lower, upper, ticks = vr_scaled_bounds(values)
            self.assertLess(lower, upper)
            self.assertTrue(all(type(tick) is int for tick in ticks))
            for value in values:
                if value is not None:
                    self.assertLessEqual(lower, value)
                    self.assertGreaterEqual(upper, value)

    def test_png_numbers_courses_in_saved_order(self):
        from mkw_tracker.report_fonts import draw_text
        self.race('a', 'First entry')
        self.race('b', 'Second entry')
        self.store.reorder_history(self.folder, ['b', 'a'], ['a', 'b'])
        with patch("mkw_tracker.ww_reports.draw_text", wraps=draw_text) as draw:
            self.image()
        labels = [call.args[2].split() for call in draw.call_args_list]
        self.assertIn(['R1', 'Second', 'entry'], labels)
        self.assertIn(['R2', 'First', 'entry'], labels)

    def test_png_pending_long_unicode_and_player_font(self):
        name = "選手☆" * 60
        self.store.set_player_name(name, self.session["id"])
        self.store.rename_folder(self.folder, "日本語セッション" * 20)
        self.race("a", "長いコース名" * 30, position=24, vr_after=8759)
        self.race("pending", "Rainbow Road")
        from mkw_tracker.report_fonts import draw_text
        with patch("mkw_tracker.ww_reports.draw_text", wraps=draw_text) as draw:
            self.image()
        player_calls = [call for call in draw.call_args_list if "選" in call.args[2]]
        self.assertTrue(player_calls)
        self.assertTrue(all(call.kwargs["font"].getname()[0] != "Racers Delight" for call in player_calls))
        labels = " ".join(call.args[2] for call in draw.call_args_list)
        self.assertIn("Unknown", labels)
        self.assertIn("Pending", labels)
        self.assertNotIn("984", labels)
        self.assertNotIn("Team", labels)

    def test_png_omits_lobby_without_changing_saved_results(self):
        self.race("known", position=5, vr_before=8000, vr_after=8050, lobby_size=23)
        self.race("unknown", position=2, vr_after=8100)
        state = self.store.snapshot()
        from mkw_tracker.report_fonts import draw_text
        with patch("mkw_tracker.ww_reports.draw_text", wraps=draw_text) as draw:
            self.image(state)
        labels = [call.args[2] for call in draw.call_args_list]
        for label in ("Race / course", "Place", "VR change", "Final VR", "+50"):
            self.assertIn(label, labels)
        self.assertNotIn("Lobby", labels)
        self.assertNotIn("23", labels)
        # Only the second race's change is unknown, not a hidden lobby column.
        self.assertEqual(labels.count("Unknown"), 1)
        self.assertEqual(self.store.snapshot(), state)

    def test_uploaded_icon_is_composited_without_bundled_artwork(self):
        from PIL import Image
        from mkw_tracker.themes import resolve_theme
        theme = resolve_theme('mario')
        content = BytesIO()
        icon = Image.new('RGBA', (32, 32), (20, 80, 160, 128))
        icon.save(content, format='PNG')
        theme.update(id='custom-' + '0' * 32, custom=True, icon_name=theme['name'],
                     icon_url='/api/themes/custom-' + '0' * 32 + '/icon.png?v=' + '0' * 16,
                     icon_png=content.getvalue())
        payload = render_ww_folder_png(self.store.snapshot(), self.folder, theme)
        expected = Image.new('RGB', icon.size, theme['tokens']['bg'])
        expected.paste(icon, (0, 0), icon)
        with Image.open(BytesIO(payload)) as image:
            self.assertEqual(image.crop((52, 44, 84, 76)).tobytes(), expected.tobytes())

    def test_png_course_names_without_game_art_or_helper_text(self):
        from mkw_tracker.report_fonts import draw_text
        self.race("known", position=5, vr_before=8000, vr_after=8050)
        with patch("mkw_tracker.ww_reports.draw_text", wraps=draw_text) as draw:
            self.image()
        labels = [call.args[2] for call in draw.call_args_list]
        for removed in ("Gaps mark", "Averages exclude", "Samples show", "WW tracks",
                        "Results use the latest saved corrections"):
            self.assertFalse(any(removed in label for label in labels))
        course_labels = [call for call in draw.call_args_list if call.args[2].endswith("Rainbow Road")]
        self.assertEqual(len(course_labels), 2)
        self.assertTrue(all(call.args[1][0] == 48 for call in course_labels))

    def test_png_course_ranking_uses_mean_position_and_standings_terminology(self):
        from mkw_tracker.report_fonts import draw_text
        self.race('large-gain', 'Large VR gain', position=12, vr_before=8000, vr_after=8200)
        self.race('better-a', 'Better places', position=2, vr_before=8200, vr_after=8190)
        self.race('better-b', 'Better places', position=4, vr_before=8190, vr_after=8180)
        with patch('mkw_tracker.ww_reports.draw_text', wraps=draw_text) as draw:
            self.image()
        labels = [call.args[2] for call in draw.call_args_list]
        self.assertIn('Best courses by mean position', labels)
        self.assertIn('Mean position', labels)
        self.assertIn('Mean VR change', labels)
        self.assertLess(labels.index('Better places'), labels.index('Large VR gain'))
        for old in ('Best courses by average known VR change', 'Avg. place', 'Avg. change'):
            self.assertNotIn(old, labels)

    def test_png_unknown_course_keeps_wrapped_text_without_placeholder_art(self):
        from mkw_tracker.report_fonts import draw_text
        self.race("known", position=5, vr_after=8050)
        self.race("unknown", "Unrecognized course " * 8, position=2, vr_after=8100)
        with patch("mkw_tracker.ww_reports.draw_text", wraps=draw_text) as draw:
            self.image()
        labels = [call.args[2] for call in draw.call_args_list]
        self.assertNotIn("?", labels)
        self.assertTrue(any("Rainbow Road" in label for label in labels))
        wrapped = [call for call in draw.call_args_list if "Unrecognized" in call.args[2]]
        self.assertGreater(len(wrapped), 2)
        self.assertTrue(all(call.args[1][0] == 48 for call in wrapped))

    def test_report_has_bounded_image_size_for_many_races(self):
        self.race("sample", position=1, vr_before=8000, vr_after=8050)
        state = self.store.snapshot()
        race = state["races"][0]
        state["races"] = [dict(deepcopy(race), id=f"r{i}") for i in range(80)]
        self.image(state)
        state["races"] = [dict(race, id=f"r{i}") for i in range(MAX_REPORT_RACES + 1)]
        with self.assertRaisesRegex(ValueError, "at most"):
            render_ww_folder_png(state, self.folder)

    def test_tall_report_rejected_before_allocating_image(self):
        self.race("sample", "長" * 200, position=1, vr_after=9000)
        state = self.store.snapshot()
        race = state["races"][0]
        state["races"] = [dict(race, id=f"r{i}") for i in range(MAX_REPORT_RACES)]
        try:
            import PIL.Image
        except ImportError:
            self.skipTest("Pillow is not installed")
        with patch.object(PIL.Image, "new", side_effect=AssertionError("must not allocate")):
            try:
                with self.assertRaisesRegex(ValueError, "too tall"):
                    render_ww_folder_png(state, self.folder)
            except RuntimeError as error:
                if "Japanese-capable font" in str(error):
                    self.skipTest(str(error))
                raise

    def test_invalid_folder_and_non_ww_snapshots_rejected(self):
        with self.assertRaises(KeyError):
            ww_folder_report(self.store.snapshot(), "missing")
        state = self.store.snapshot()
        state["tracker_mode"] = "mogi"
        with self.assertRaisesRegex(ValueError, "WW session"):
            ww_folder_report(state, self.folder)


if __name__ == "__main__":
    unittest.main()
