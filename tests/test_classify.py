import unittest

from shortlist.classify import is_us, seniority


class IsUSTest(unittest.TestCase):
    def test_us_places(self):
        for loc in ["San Jose, CA", "Indianapolis, IN", "Albuquerque, New Mexico", "Portland, OR", "US", "Remote",
                    "Austin, TX or Remote", "San Francisco, CA | New York City, NY"]:
            with self.subTest(loc=loc):
                self.assertTrue(is_us(loc))

    def test_non_us_places(self):
        for loc in ["London, UK", "Bangalore, India", "Remote - Brazil", "San Jose, CR", "Mexico City, Mexico",
                    "France, Remote; Spain, Remote", ""]:
            with self.subTest(loc=loc):
                self.assertFalse(is_us(loc))

    def test_any_us_place_in_a_multi_location_posting(self):
        self.assertTrue(is_us("San Francisco; New York City; London, UK"))
        self.assertTrue(is_us("London; New York; Remote"))
        self.assertFalse(is_us("Delhi, India; Mumbai, India; Bangalore - Remote"))

    def test_title(self):
        # Region words must match whole words: "Capacity" is not APAC.
        self.assertTrue(is_us("San Francisco", title="Capacity Systems Software Engineer"))
        self.assertFalse(is_us("Ontario - Remote", title="AI Support Engineer - Toronto"))


class SeniorityTest(unittest.TestCase):
    def test_hospital_and_university_titles(self):
        # Hospital, pharmacy and university boards are full of titles that match role-family words.
        for title, expected in (("Postdoctoral Research Associate", "postdoc"), ("Post Doc Research Scientist", "postdoc"),
                                ("NVIDIA 2027 Internships: Software Engineering", "internship"),
                                ("Beauty Sales Consultant", "off_profile"), ("Lactation Consultant RN", "off_profile"),
                                ("QM Nurse Consultant", "off_profile"),
                                ("Clinical Research Associate/Coordinator", "off_profile"),
                                ("Adjunct Faculty, Bioinformatics", "off_profile"),
                                ("Equity Research Associate - Banks", "off_profile"), ("Financial Planning Consultant", "off_profile"),
                                ("Executive Partner (CIO Advisory)", "senior")):
            with self.subTest(title=title):
                self.assertEqual(seniority(title), expected)

    def test_data_titles_are_unchanged(self):
        for title, expected in (("Research Data Analyst", "mid"), ("Clinical Data Analyst", "mid"),
                                ("Associate Data Scientist", "entry"), ("Senior Data Engineer", "senior"),
                                ("Software Engineering Intern", "internship"), ("Partner AI Solution Consultant", "mid"),
                                ("Human Resources Data Analyst", "mid")):
            with self.subTest(title=title):
                self.assertEqual(seniority(title), expected)


if __name__ == "__main__":
    unittest.main()
