"""Wage areas: which OFLC area a posting's place gets its prevailing wages from (data/raw/OFLC_Wages_2026-27.zip)."""

import unittest

from shortlist import classify, wages

HOME = "home"


def area(place: str) -> str:
    return wages.area_for(place, None, home_area=HOME)


@unittest.skipUnless(wages.WAGE_ZIP.exists(), "the OFLC wage file isn't downloaded (python -m shortlist data)")
class AreaTest(unittest.TestCase):
    def test_city_and_state_uses_the_area_named_after_the_city(self):
        for place in ("Atlanta, GA", "Boston, MA (Main Campus)", "Santa Clara, CA; Hillsboro, OR"):
            with self.subTest(place=place):
                self.assertNotEqual(area(place), HOME)
        self.assertEqual(area("Santa Clara, CA; Hillsboro, OR"), area("San Jose, CA"))

    def test_workday_site_codes_and_other_formats(self):
        for place, same_as in (("US GA ATL 201 STE 900", "Atlanta, GA"), ("GA Atlanta 1050 Techwood Drive NW", "Atlanta, GA"),
                               ("US-VA Richmond - CoStar Tower", "Richmond, VA"), ("USA - Buffalo - New York", "Buffalo, NY"),
                               ("Seattle Campus, WA", "Seattle, WA"), ("Austin, Texas", "Austin, TX"),
                               ("Sunnyvale (US)", "Sunnyvale, CA"), ("Kansas City, Kansas", "Kansas City, KS"),
                               ("Washington DC", "Washington, DC")):
            with self.subTest(place=place):
                self.assertEqual(area(place), area(same_as))

    def test_a_city_no_area_is_named_after_uses_its_states_main_metro(self):
        for place, same_as in (("Milpitas, CA", "San Jose, CA"), ("Plano, TX", "Dallas, TX"), ("Malvern, PA", "Philadelphia, PA"),
                               ("Bethesda, MD", "Washington, DC"), ("USA.VA.Reston", "Washington, DC"),
                               ("Brooklyn, NY", "New York, NY")):
            with self.subTest(place=place):
                self.assertEqual(area(place), area(same_as))

    def test_every_state_has_a_main_metro_in_it(self):
        self.assertEqual(set(wages.MAIN_CITY), classify.US_STATES)
        for state, city in wages.MAIN_CITY.items():
            with self.subTest(state=state):
                self.assertIsNotNone(wages._named(city, state))

    def test_remote_foreign_and_unplaceable_postings_use_the_home_area(self):
        for place in ("Remote - US", "Work At Home-Delaware; Work At Home-Idaho", "Berlin, DE", "Hyderabad, IN", "Toronto, ON",
                      "Medellin, Colombia", "United States of America", "2 Locations", "Springfield", ""):
            with self.subTest(place=place):
                self.assertEqual(area(place), HOME)

    def test_an_atlanta_posting_is_measured_against_atlanta_wages(self):
        # FIS's "US GA ATL 201 STE 900" used to get Boston's wages, so $110,000 read as Level I instead of Level II.
        self.assertEqual(wages.wage_level(110_000, "15-2051", area("US GA ATL 201 STE 900")), 2)


if __name__ == "__main__":
    unittest.main()
