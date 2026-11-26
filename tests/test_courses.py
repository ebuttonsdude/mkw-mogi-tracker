import unittest

from mkw_tracker.courses import COURSES, match_course


NUMBERED_VARIANTS = (
    'Airship Fortress 2', 'Desert Hills 2', 'Salty Salty Speedway 2', 'DK Pass 2',
    'Koopa Troopa Beach 2', 'Shy Guy Bazaar 2', 'Mario Bros. Circuit 2', 'Wario Stadium 2',
)


class CourseTests(unittest.TestCase):
    def test_all_user_courses_and_case_punctuation(self):
        self.assertEqual(len(COURSES), 48)
        self.assertEqual(len(set(COURSES)), len(COURSES))
        for course in COURSES:
            with self.subTest(course=course):
                self.assertEqual(match_course(course), course)
                self.assertEqual(match_course(course.upper().replace(' ', '')), course)

    def test_partial_long_sample_reads_are_resolved_uniquely(self):
        for text, expected in [('Guy Bazaar 2', 'Shy Guy Bazaar 2'), ('Sundae', 'Sky-High Sundae'),
                               ('Great Block', 'Great ? Block Ruins'),
                               ('snes Ghost Valley 2', 'SNES Ghost Valley 2'),
                               ('SNES Koopa', 'SNES Koopa Beach 1'),
                               ('Bowsers Caslle', "Bowser's Castle")]:
            self.assertEqual(match_course(text), expected)

    def test_numbered_variants_remain_distinct_from_originals(self):
        for course in NUMBERED_VARIANTS:
            with self.subTest(course=course):
                self.assertIn(course, COURSES)
                self.assertEqual(match_course(course), course)
                self.assertEqual(match_course(course[:-2]), course[:-2])
                self.assertIsNone(match_course(course[:-1] + '3'))

    def test_numbers_and_ambiguous_or_unknown_reads_are_not_guessed(self):
        for text in ['', 'Ghost Valley', 'SNES Ghost Valley', 'SNES Mario Circuit',
                     'SNES Mario Circuit 4', 'SNES Choco Island', 'asldkfufvo', 'Road',
                     'Guy Bazaar', 'Airship Fort', 'Salty Salty']:
            self.assertIsNone(match_course(text), text)
        self.assertEqual(match_course('Ghost Valley 3'), 'SNES Ghost Valley 3')


if __name__ == '__main__':
    unittest.main()
