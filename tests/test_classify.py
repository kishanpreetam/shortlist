import unittest

from shortlist.classify import is_us


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


if __name__ == "__main__":
    unittest.main()
