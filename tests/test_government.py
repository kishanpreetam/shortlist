import unittest

from shortlist.government import is_government


class GovernmentEmployers(unittest.TestCase):
    def test_counts_as_government(self):
        for name in ("Iowa State University", "University of California, San Diego", "UCSF Health", "SUNY Buffalo",
                     "Montgomery County", "County of Los Angeles", "Department of Veterans Affairs", "City of Boston",
                     "Fannie Mae", "Freddie Mac", "Booz Allen Hamilton", "Leidos", "MITRE", "Lawrence Livermore National Laboratory",
                     "Fairfax County Public Schools", "University of Michigan"):
            with self.subTest(name=name):
                self.assertTrue(is_government(name), name)

    def test_private_stays(self):
        for name in ("Northeastern University", "Harvard University", "University of Chicago", "Boston University",
                     "Mass General Brigham", "U.S. Bank", "Government Employees Insurance Company", "Orange County Credit Union",
                     "Anthropic", "Amazon"):
            with self.subTest(name=name):
                self.assertFalse(is_government(name), name)


if __name__ == "__main__":
    unittest.main()
