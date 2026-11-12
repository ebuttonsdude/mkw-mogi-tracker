import unittest

from mkw_tracker.courses import COURSES, match_course


class CourseTests(unittest.TestCase):
    def test_all_user_courses_and_case_punctuation(self):
        self.assertEqual(len(COURSES), 40)
        for course in COURSES:
            with self.subTest(course=course):
                self.assertEqual(match_course(course), course)
                self.assertEqual(match_course(course.upper().replace(' ', '')), course)

    def test_partial_long_sample_reads_are_resolved_uniquely(self):
        for text, expected in [('Guy Bazaar', 'Shy Guy Bazaar'), ('Sundae', 'Sky-High Sundae'),
                               ('Great Block', 'Great ? Block Ruins'),
                               ('snes Ghost Valley 2', 'SNES Ghost Valley 2'),
                               ('SNES Koopa', 'SNES Koopa Beach 1'),
                               ('Bowsers Caslle', "Bowser's Castle")]:
            self.assertEqual(match_course(text), expected)

    def test_numbers_and_ambiguous_or_unknown_reads_are_not_guessed(self):
        for text in ['', 'Ghost Valley', 'SNES Ghost Valley', 'SNES Mario Circuit',
                     'SNES Mario Circuit 4', 'SNES Choco Island', 'asldkfufvo', 'Road']:
            self.assertIsNone(match_course(text), text)
        self.assertEqual(match_course('Ghost Valley 3'), 'SNES Ghost Valley 3')


if __name__ == '__main__':
    unittest.main()
