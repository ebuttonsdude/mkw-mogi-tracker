import unittest

from mkw_tracker.chapters import format_resolve_edl


class ResolveMarkerTests(unittest.TestCase):
    def test_complete_edl_preserves_names_timecodes_and_event_count(self):
        examples = [
            (0, "Bowser's Castle", "01:00:00:00", "01:00:00:01"),
            (45_849, "Salty Salty Speedway", "01:00:45:51", "01:00:45:52"),
            (270_249, "Great ? Block Ruins", "01:04:30:15", "01:04:30:16"),
            (2_586_299, "お城", "01:43:06:18", "01:43:06:19"),
            (2_586_299, "お城", "01:43:06:18", "01:43:06:19"),
        ]
        rows = [{"course": name, "offset_ms": ms} for ms, name, _, _ in examples]
        expected = ["TITLE: Race chapters\r\nFCM: NON-DROP FRAME"]
        for index, (_, name, start, end) in enumerate(examples, 1):
            expected.append(f"{index:03}  001      V     C        {start} {end} {start} {end}\r\n"
                            f" |C:ResolveColorBlue |M:{name} |D:1")
        self.assertEqual(format_resolve_edl(rows), ("\r\n\r\n".join(expected) + "\r\n").encode("utf-8"))

    def test_half_up_rounding_and_rollover(self):
        for ms, start, end in [
            (25, "01:00:00:02", "01:00:00:03"),  # exactly 1.5 frames
            (991, "01:00:00:59", "01:00:01:00"),
            (992, "01:00:01:00", "01:00:01:01"),
            (59_991, "01:00:59:59", "01:01:00:00"),
            (3_599_991, "01:59:59:59", "02:00:00:00"),
            (3_600_000, "02:00:00:00", "02:00:00:01"),
        ]:
            with self.subTest(ms=ms):
                edl = format_resolve_edl([{"course": "Race", "offset_ms": ms}])
                self.assertIn(f"{start} {end} {start} {end}\r\n".encode(), edl)

    def test_event_numbers_continue_past_three_digits(self):
        edl = format_resolve_edl([{"course": "Race", "offset_ms": 0}] * 1000)
        self.assertIn(b"\r\n1000  001      V     C", edl)

    def test_rejects_whole_export_if_any_timestamp_is_invalid(self):
        for fields in [{}, {"offset_ms": None}, {"offset_ms": -1}, {"offset_ms": True},
                       {"offset_ms": 0.5}, {"offset_ms": "00:01"}, {"offset_ms": 100 * 3_600_000}]:
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, r"Race 2 \(Castle\): .*timestamp"):
                format_resolve_edl([{"course": "OK", "offset_ms": 0}, {"course": "Castle", **fields}])

    def test_metadata_cannot_inject_lines_or_fields(self):
        for name in ["one\rnext", "one\nnext", "one|D:99", "one\x00", "one\u2028", "one\u2029", "", None]:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Race 1: .*course name"):
                format_resolve_edl([{"course": name, "offset_ms": 0}])

    def test_empty_recording_offers_correction(self):
        with self.assertRaisesRegex(ValueError, "Assign a race"):
            format_resolve_edl([])
