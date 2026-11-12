import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from mkw_tracker.store import TrackerStore
from mkw_tracker.ww_reports import ww_folder_report
from mkw_tracker.ww_store import WWStore


class WWStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ww.sqlite"
        self.store = WWStore(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def race(self, identifier="r1", course="Rainbow Road", **values):
        self.store.create_race(identifier, course)
        if values:
            self.store.set_ww_result(identifier, **values)
        return next(r for r in self.store.snapshot()["races"] if r["id"] == identifier)

    def test_empty_session_has_no_invented_vr_or_mogi_scores(self):
        state = self.store.snapshot()
        self.assertEqual(state["tracker_mode"], "ww")
        self.assertEqual(state["players"], [])
        self.assertEqual(state["teams"], [])
        for key in ("current_vr", "starting_vr", "peak_vr", "lowest_vr", "total_change", "known_change_sum"):
            self.assertIsNone(state["ww"][key])
        self.assertEqual(state["ww"]["graph"], [])
        self.assertEqual(state["ww"]["courses"], [])
        self.assertEqual(state["room"]["points"], 0)
        self.assertIsNone(state["room"]["maximum_points"])
        self.assertIsNone(state["room"]["expected_races"])

    def test_observed_baseline_extrema_and_repeated_results(self):
        first = self.race(position=18, vr_after=8759, vr_before=8805, delta=-46, lobby_size=23)
        self.assertEqual(first["status"], "recorded")
        self.assertEqual(first["placements"], [])
        again = self.store.set_ww_result("r1", position=18, vr_after=8759, delta=-46, lobby_size=23)
        self.assertEqual(first, again)
        self.race("r2", position=16, vr_after=8736, vr_before=8759, lobby_size=24)
        ww = self.store.snapshot()["ww"]
        self.assertEqual((ww["starting_vr"], ww["current_vr"], ww["peak_vr"], ww["lowest_vr"]),
                         (8805, 8736, 8805, 8736))
        self.assertEqual(ww["total_change"], -69)
        self.assertEqual(ww["known_changes"], 2)
        self.assertEqual([p["vr"] for p in ww["graph"]], [8805, 8759, 8736])
        self.assertFalse(any(p["gap_before"] for p in ww["graph"]))
        self.assertEqual(ww["courses"][0]["average_change"], -34.5)
        self.assertEqual(ww["courses"][0]["average_position"], 17)
        self.assertEqual(len(self.store._rows("SELECT * FROM ww_results")), 2)

    def test_course_statistics_rank_mean_position_before_vr_change(self):
        session = self.store.snapshot()["session"]
        self.race("best", "Rainbow Road", position=1, vr_after=9000)
        self.race("worse", "Mario Circuit", position=8, vr_after=9050, delta=100)
        self.race("tie-z", "Z course", position=4, vr_after=9000, delta=200)
        self.race("tie-a", "A course", position=4, vr_after=9000, delta=-200)
        self.race("average", "Rainbow Road", position=3, vr_after=9000, delta=-50)
        self.race("unknown", "Crown City", position=1, vr_after=9000)
        state = self.store.snapshot()
        expected = ["Crown City", "Rainbow Road", "A course", "Z course", "Mario Circuit"]
        self.assertEqual([course["course"] for course in state["ww"]["courses"]], expected)
        self.assertIsNone(state["ww"]["courses"][0]["average_change"])
        self.assertEqual(state["ww"]["courses"][1]["average_position"], 2)
        self.assertEqual([course["course"] for course in ww_folder_report(state, session["folder_id"])["courses"]],
                         expected)
        self.store.create_session("Other WW", session["id"])
        self.race("other", "New course", position=3, vr_after=9000, delta=500)
        self.assertEqual([course["course"] for course in self.store.snapshot()["ww"]["all_courses"]],
                         ["Crown City", "Rainbow Road", "New course", "A course", "Z course", "Mario Circuit"])

    def test_last_ten_change_uses_available_results_and_preserves_zero_and_negative(self):
        ww = self.store.snapshot()["ww"]
        self.assertIsNone(ww["last_10_change"])
        self.assertEqual(ww["last_10_race_count"], 0)
        self.race("first", position=1, vr_after=9000, delta=25)
        self.assertEqual(self.store.snapshot()["ww"]["last_10_change"], 25)
        self.race("second", position=2, vr_after=9000, delta=-25)
        self.assertEqual(self.store.snapshot()["ww"]["last_10_change"], 0)
        self.race("third", position=3, vr_after=9000, delta=-10)
        self.race("pending")
        ww = self.store.snapshot()["ww"]
        self.assertEqual(ww["last_10_change"], -10)
        self.assertEqual(ww["last_10_race_count"], 3)
        self.assertIsNone(ww["total_change"])

    def test_last_ten_change_keeps_unknown_until_it_leaves_window(self):
        self.race("unknown", position=1, vr_after=9000)
        for index in range(9):
            self.race(f"known-{index}", position=1, vr_after=9000, delta=index)
        self.assertIsNone(self.store.snapshot()["ww"]["last_10_change"])
        # A pending race does not displace a completed unknown result.
        self.race("pending")
        self.assertIsNone(self.store.snapshot()["ww"]["last_10_change"])
        self.race("eleventh", position=1, vr_after=9000, delta=-5)
        ww = self.store.snapshot()["ww"]
        self.assertEqual(ww["last_10_change"], 31)
        self.assertEqual(ww["last_10_race_count"], 10)

    def test_last_ten_change_updates_corrections_deletions_and_session_selection(self):
        original = self.store.snapshot()["session"]
        for index in range(12):
            self.race(f"race-{index}", position=1, vr_after=9000, delta=index)
        self.assertEqual(self.store.snapshot()["ww"]["last_10_change"], sum(range(2, 12)))
        # Corrections must not change chronological race order.
        self.store.set_ww_result("race-0", position=1, vr_after=9000, delta=1000)
        self.store.set_ww_result("race-11", position=1, vr_after=9000, delta=-11)
        self.assertEqual(self.store.snapshot()["ww"]["last_10_change"], sum(range(2, 11)) - 11)
        self.store.delete_race("race-11")
        expected = sum(range(1, 11))
        self.assertEqual(self.store.snapshot()["ww"]["last_10_change"], expected)
        current = self.store.create_session("Other WW", original["id"])
        self.assertIsNone(self.store.snapshot()["ww"]["last_10_change"])
        self.assertEqual(self.store.snapshot()["ww"]["last_10_race_count"], 0)
        self.race("other", position=1, vr_after=9000, delta=-20)
        self.assertEqual(self.store.snapshot()["ww"]["last_10_change"], -20)
        self.store.load_session(original["folder_id"], current["id"])
        self.assertEqual(self.store.snapshot()["ww"]["last_10_change"], expected)

    def test_correction_replaces_contribution_and_retains_revisions(self):
        first = self.race(position=5, vr_before=8576, vr_after=8628)
        self.race("r2", position=1, vr_before=8628, vr_after=8641)
        corrected = self.store.set_ww_result("r1", position=6, vr_before=8576, vr_after=8620)
        self.assertEqual(corrected["revision"], 2)
        self.assertEqual(corrected["confirmed_order"], first["confirmed_order"])
        self.assertEqual(self.store.snapshot()["ww"]["current_vr"], 8641)
        self.assertEqual(self.store.snapshot()["ww"]["known_change_sum"], 57)
        self.assertTrue(self.store.snapshot()["ww"]["graph"][-1]["gap_before"])
        revisions = self.store._rows("SELECT * FROM ww_results WHERE race_id='r1' ORDER BY revision")
        self.assertEqual([r["vr_after"] for r in revisions], [8628, 8620])
        self.assertEqual(self.store._rows("SELECT * FROM placements"), [])

    def test_cut_result_saves_known_values_without_borrowing_previous_vr(self):
        self.race(position=1, vr_before=8600, vr_after=8650)
        result = self.race("r2", position=16, vr_after=8736)["ww_result"]
        self.assertIsNone(result["vr_before"])
        self.assertIsNone(result["delta"])
        self.assertIsNone(result["lobby_size"])
        ww = self.store.snapshot()["ww"]
        self.assertEqual(ww["current_vr"], 8736)
        self.assertEqual(ww["known_changes"], 1)
        self.assertEqual(ww["known_change_sum"], 50)
        self.assertIsNone(ww["total_change"])
        self.assertTrue(ww["graph"][-1]["gap_before"])
        self.assertEqual(ww["courses"][0]["races"], 2)
        self.assertEqual(ww["courses"][0]["known_changes"], 1)
        self.assertEqual(ww["courses"][0]["average_change"], 50)

    def test_live_changes_follow_previous_recorded_final_and_latest_correction(self):
        self.race("first", position=1, vr_before=8000, vr_after=8050,
                  source="capture", derive_from_previous=True)
        self.race("pending")
        self.race("second", position=2, vr_before=8070, delta=10, vr_after=8080,
                  source="capture", derive_from_previous=True)
        self.race("third", position=3, vr_after=8060, source="capture", derive_from_previous=True)
        self.race("fourth", position=4, vr_after=8060, source="capture", derive_from_previous=True)
        results = [r["ww_result"] for r in self.store.snapshot()["races"] if r["ww_result"]]
        self.assertEqual([r["delta"] for r in results], [50, 30, -20, 0])
        self.store.set_ww_result("first", position=1, vr_before=8000, vr_after=8040)
        second = self.store._race("second")
        self.assertEqual((second["ww_result"]["vr_before"], second["ww_result"]["delta"]), (8040, 40))
        self.assertEqual(second["revision"], 1)
        # Raw observations and revision history stay intact; derivation is a view.
        observed = self.store._rows("SELECT * FROM ww_results WHERE race_id='second'")[0]
        self.assertEqual((observed["vr_before"], observed["delta"]), (8070, 10))
        self.assertEqual(self.store.set_ww_result("second", position=2, vr_before=8070, delta=10,
                         vr_after=8080, source="capture", derive_from_previous=True)["revision"], 1)

    def test_live_derivation_reopens_and_manual_override_stays_fixed(self):
        self.race("first", position=1, vr_after=8000)
        self.race("second", position=2, vr_after=8050, source="capture", derive_from_previous=True)
        self.store.close()
        self.store = WWStore(self.path)
        self.assertEqual(self.store._race("second")["ww_result"]["delta"], 50)
        self.store.set_ww_result("second", position=3, vr_before=8040, vr_after=8050)
        self.store.set_ww_result("first", position=1, vr_after=7990)
        result = self.store._race("second")["ww_result"]
        self.assertEqual(result["delta"], 10)
        self.assertFalse(result["derive_from_previous"])

    def test_live_first_unknown_baseline_and_deleted_predecessor_do_not_leak_values(self):
        first = self.race("first", position=1, vr_after=8000, source="capture",
                          derive_from_previous=True)["ww_result"]
        self.assertIsNone(first["vr_before"])
        self.assertIsNone(first["delta"])
        self.race("second", position=2, vr_after=8050, source="capture", derive_from_previous=True)
        self.store.delete_race("first")
        self.assertIsNone(self.store._race("second")["ww_result"]["delta"])
        # A later race cannot act as an earlier race's baseline.
        self.race("third", position=3, vr_after=8100, source="capture", derive_from_previous=True)
        self.assertIsNone(self.store._race("second")["ww_result"]["delta"])

    def test_live_derivation_never_crosses_sessions_or_changes_imports(self):
        owner = self.store.snapshot()["session"]
        self.race("first", position=1, vr_after=8000)
        self.race("import", position=2, vr_after=8050, source="import")
        self.assertIsNone(self.store._race("import")["ww_result"]["delta"])
        self.store.create_session("Other", owner["id"])
        self.race("new-first", position=1, vr_after=9000, source="capture", derive_from_previous=True)
        self.assertIsNone(self.store._race("new-first")["ww_result"]["delta"])
        self.store.create_race("old-live", "Rainbow Road", session_id=owner["id"], folder_id=owner["folder_id"])
        result = self.store.set_ww_result("old-live", position=2, vr_after=8100,
                                         source="capture", derive_from_previous=True)["ww_result"]
        self.assertEqual((result["vr_before"], result["delta"]), (8050, 50))

    def test_derivation_flag_requires_live_capture_and_boolean(self):
        self.race()
        before = self.store.snapshot()
        for source, flag in [("manual", True), ("import", True), ("automatic", True),
                             ("capture", 1), ("capture", None), ("capture", "yes")]:
            with self.subTest(source=source, flag=flag), self.assertRaises(ValueError):
                self.store.set_ww_result("r1", position=1, vr_after=9000,
                                         source=source, derive_from_previous=flag)
            self.assertEqual(self.store.snapshot(), before)

    def test_existing_database_adds_disabled_derivation_flag_without_rewriting_values(self):
        self.race("first", position=1, vr_after=8000, source="capture")
        self.race("second", position=2, vr_after=8050, source="capture")
        self.store.close()
        connection = sqlite3.connect(self.path)
        try:
            connection.executescript("""CREATE TABLE legacy_results AS SELECT
                race_id,revision,position,lobby_size,vr_before,vr_after,delta,source,created_at FROM ww_results;
                DROP TABLE ww_results;
                ALTER TABLE legacy_results RENAME TO ww_results;""")
        finally:
            connection.close()
        self.store = WWStore(self.path)
        result = self.store._race("second")["ww_result"]
        self.assertFalse(result["derive_from_previous"])
        self.assertIsNone(result["delta"])
        self.assertEqual(self.store._race("second")["revision"], 1)

    def test_unknown_first_baseline_and_pending_race_remain_unknown(self):
        self.race(position=3, vr_after=9000)
        self.race("pending", "Mario Circuit")
        self.race("r3", position=2, vr_before=9050, vr_after=9060)
        ww = self.store.snapshot()["ww"]
        self.assertIsNone(ww["starting_vr"])
        self.assertEqual(ww["lowest_vr"], 9000)
        self.assertEqual(ww["peak_vr"], 9060)
        self.assertEqual(ww["recorded_races"], 2)
        self.assertEqual(ww["pending_races"], 1)
        self.assertEqual([p["vr"] for p in ww["graph"]], [9000, None, 9060])
        self.assertTrue(ww["graph"][-1]["gap_before"])
        self.assertIsNone(ww["total_change"])
        self.assertEqual([c["course"] for c in ww["courses"]], ["Rainbow Road"])

    def test_zero_delta_and_small_lobby_are_valid(self):
        result = self.race(position=2, vr_after=1, delta=0, lobby_size=2)["ww_result"]
        self.assertEqual(result["vr_before"], 1)
        self.assertEqual(result["delta"], 0)
        self.assertEqual(self.store.snapshot()["ww"]["total_change"], 0)
        self.assertEqual(self.store.snapshot()["ww"]["courses"][0]["average_change"], 0)
        self.race("max", position=24, lobby_size=24, vr_after=99999)

    def test_invalid_results_do_not_mutate(self):
        self.race()
        before = self.store.snapshot()
        invalid = []
        for field, values in {
            "position": (None, 0, 25, True, 1.0, "1"),
            "lobby_size": (0, 1, 25, True, 2.0, "24"),
            "vr_after": (None, 0, 100000, True, 5.0, "9000"),
            "vr_before": (0, 100000, True, 5.0, "9000"),
            "delta": (-99999, 99999, True, 1.0, "1"),
            "source": (None, [], "model guessed"),
        }.items():
            invalid.extend({field: value} for value in values)
        invalid += [{"position": 24, "lobby_size": 12}, {"vr_before": 8000, "delta": 4},
                    {"delta": 9000}, {"vr_after": 99999, "delta": -1}]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.store.set_ww_result("r1", **{"position": 1, "vr_after": 9000, **values})
            self.assertEqual(self.store.snapshot(), before)

    def test_new_sessions_isolate_labels_results_and_statistics(self):
        old = self.store.snapshot()["session"]
        self.store.set_player_name("★プレイヤー", old["id"])
        self.race(position=1, vr_before=9000, vr_after=9050)
        current = self.store.create_session("Second WW", old["id"])
        self.assertEqual(self.store.snapshot()["ww"]["player_name"], "Local player")
        self.assertIsNone(self.store.snapshot()["ww"]["current_vr"])
        self.assertEqual(self.store.snapshot()["ww"]["courses"], [])
        self.assertEqual(len(self.store.snapshot()["ww"]["all_courses"]), 1)
        self.store.set_player_name("New player", current["id"])
        self.race("r2", "Mario Circuit", position=2, vr_after=4000)
        self.store.load_session(old["folder_id"], current["id"])
        self.assertEqual(self.store.snapshot()["ww"]["player_name"], "★プレイヤー")
        self.assertEqual(self.store.snapshot()["ww"]["current_vr"], 9050)
        self.assertEqual(len(self.store.snapshot()["ww"]["courses"]), 1)
        self.assertEqual(len(self.store.snapshot()["ww"]["all_courses"]), 2)

    def test_stale_or_cross_session_manual_writes_rejected_but_fixed_capture_allowed(self):
        old = self.store.snapshot()["session"]
        self.race()
        current = self.store.create_session("Other tab", old["id"])
        before = self.store.snapshot()
        for expected in (None, "", old["id"], 42):
            with self.subTest(expected=expected), self.assertRaisesRegex(ValueError, "session changed"):
                self.store.set_ww_result("r1", position=1, vr_after=9000, expected_session_id=expected)
            with self.assertRaisesRegex(ValueError, "session changed"):
                self.store.set_player_name("Stale", expected)
            self.assertEqual(self.store.snapshot(), before)
        with self.assertRaisesRegex(ValueError, "another WW session"):
            self.store.set_ww_result("r1", position=1, vr_after=9000, expected_session_id=current["id"])
        self.assertEqual(self.store.snapshot(), before)
        self.store.set_ww_result("r1", position=1, vr_after=9000, source="import")
        self.assertIsNone(self.store.snapshot()["ww"]["current_vr"])

    def test_player_name_validation_is_atomic(self):
        session_id = self.store.snapshot()["session"]["id"]
        before = self.store.snapshot()
        for name in (None, "", " ", "x" * 201, "a\nb", "\ud800"):
            with self.subTest(name=repr(name)), self.assertRaises(ValueError):
                self.store.set_player_name(name, session_id)
            self.assertEqual(self.store.snapshot(), before)

    def test_expected_revision_prevents_capture_overwriting_manual_correction(self):
        self.race()
        self.store.set_ww_result("r1", position=1, vr_after=9000, expected_revision=0)
        self.store.set_ww_result("r1", position=2, vr_after=8999, expected_revision=1)
        before = self.store.snapshot()
        for revision in (0, 1):
            with self.subTest(revision=revision), self.assertRaisesRegex(ValueError, "result changed"):
                self.store.set_ww_result("r1", position=1, vr_after=9000,
                                         source="automatic", expected_revision=revision)
            self.assertEqual(self.store.snapshot(), before)
        corrected = self.store.set_ww_result("r1", position=3, vr_after=8990, expected_revision=2)
        self.assertEqual(corrected["revision"], 3)

    def test_expected_revision_rejects_invalid_values_without_mutation(self):
        self.race()
        before = self.store.snapshot()
        for revision in (None, True, False, -1, 0.0, "0", 2**63):
            with self.subTest(revision=revision), self.assertRaisesRegex(ValueError, "expected_revision"):
                self.store.set_ww_result("r1", position=1, vr_after=9000, expected_revision=revision)
            self.assertEqual(self.store.snapshot(), before)

    def test_mogi_mutations_are_rejected(self):
        before = self.store.snapshot()
        actions = [lambda: self.store.add_player("A"), lambda: self.store.create_team("A"),
                   lambda: self.store.update_player("p", "A", None), lambda: self.store.delete_player("p"),
                   lambda: self.store.delete_team("t"), lambda: self.store.rename_team("t", "B"),
                   lambda: self.store.update_team("t", color=None), lambda: self.store.review_result("r", []),
                   lambda: self.store.submit_result("r", []), lambda: self.store.correct_result("r", []),
                   lambda: self.store.add_penalty("r", "p", 1), lambda: self.store.remove_penalty("p")]
        for action in actions:
            with self.assertRaisesRegex(ValueError, "unavailable in WW"):
                action()
            self.assertEqual(self.store.snapshot(), before)

    def test_delete_race_and_session_cascade_ww_history_keep_recording(self):
        session = self.store.snapshot()["session"]
        self.store.set_player_name("Keep until session deleted", session["id"])
        recording = self.store.create_recording("Original")
        self.race(position=1, vr_after=9000)
        self.store.set_ww_result("r1", position=2, vr_after=8999)
        self.store.add_evidence("r1", str(uuid4()) + ".png")
        self.store.update_race("r1", "Rainbow Road", recording["id"], 12345)
        self.race("r2", position=3, vr_after=8900)
        self.store.delete_race("r1")
        self.assertEqual(self.store._rows("SELECT * FROM ww_results WHERE race_id='r1'"), [])
        self.assertEqual(self.store.snapshot()["ww"]["current_vr"], 8900)
        self.store.delete_folder(session["folder_id"])
        self.assertEqual(self.store._rows("SELECT * FROM ww_results"), [])
        self.assertEqual(self.store._rows("SELECT * FROM ww_session_players"), [])
        self.assertEqual(self.store.snapshot()["recordings"], [recording])
        self.assertEqual(self.store._rows("PRAGMA foreign_key_check"), [])

    def test_failed_result_revision_rolls_back(self):
        self.race(position=1, vr_after=9000)
        before = self.store.snapshot()
        original = self.store._write

        def fail(sql, values=()):
            if sql.startswith("UPDATE races SET revision"):
                raise sqlite3.OperationalError("injected write failure")
            return original(sql, values)

        with patch.object(self.store, "_write", side_effect=fail), self.assertRaises(sqlite3.OperationalError):
            self.store.set_ww_result("r1", position=2, vr_after=8999)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(len(self.store._rows("SELECT * FROM ww_results")), 1)
        self.assertEqual(len(self.store._rows("SELECT * FROM result_revisions")), 1)

    def test_persistence_and_mogi_database_are_independent(self):
        mogi = TrackerStore(Path(self.temp.name) / "mogi.sqlite")
        try:
            players = [mogi.add_player(str(i))["id"] for i in range(12)]
            mogi.create_race("r1", "Rainbow Road")
            mogi.submit_result("r1", players)
            before = mogi.snapshot()
            self.race(position=24, vr_before=9000, vr_after=8950)
            session_id = self.store.snapshot()["session"]["id"]
            self.store.set_player_name("Player 日本語", session_id)
            ww = self.store.snapshot()
            self.store.close()
            self.store = WWStore(self.path)
            self.assertEqual(self.store.snapshot(), ww)
            self.assertEqual(mogi.snapshot(), before)
            self.assertEqual(mogi.snapshot()["room"]["points"], 82)
            self.assertEqual(self.store.snapshot()["room"]["points"], 0)
        finally:
            mogi.close()


if __name__ == "__main__":
    unittest.main()
