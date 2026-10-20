"""Workday fetcher, against a recorded CXS response.

tests/fixtures/workday_*.json keep the structure of real list and detail responses
(October 2026) with placeholder titles, ids and text, so no posting is republished.
"""

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import requests

from shortlist import boards

FIXTURES = Path(__file__).parent / "fixtures"
HOST = "acme.wd1.myworkdayjobs.com"
API = f"https://{HOST}/wday/cxs/acme/careers"
ROBOTS = "User-agent: *\nAllow: /careers/\nDisallow: /refreshFacet/\n"


class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code, self._payload, self.text, self.headers = status, payload, text, {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeTenant:
    """Serves robots.txt, the jobs search and job details for one tenant, and records calls."""

    def __init__(self, robots=ROBOTS, robots_status=200):
        self.robots, self.robots_status = robots, robots_status
        self.jobs = json.loads((FIXTURES / "workday_jobs.json").read_text())
        self.job = json.loads((FIXTURES / "workday_job.json").read_text())
        self.searches, self.details = [], []

    def get(self, url, **_):
        if url == f"https://{HOST}/robots.txt":
            return FakeResponse(self.robots_status, text=self.robots)
        if url.startswith(f"{API}/job/"):
            self.details.append(url[len(API):])
            return FakeResponse(payload=self.job)
        return FakeResponse(404)

    def post(self, url, json=None, **_):
        assert url == f"{API}/jobs", url
        self.searches.append(json)
        return FakeResponse(payload=self.jobs)

    def run(self):
        with mock.patch.object(boards, "MIN_INTERVAL_S", 0), \
                mock.patch.object(boards.requests, "get", self.get), \
                mock.patch.object(boards.requests, "post", self.post):
            return boards.workday("acme/wd1/careers", "Acme")


class WorkdayFetchTest(unittest.TestCase):
    def setUp(self):
        self.tenant = FakeTenant()
        self.posts = {p.title: p for p in self.tenant.run()}

    def test_detail_becomes_a_full_posting(self):
        p = self.posts["Associate Data Scientist"]
        self.assertEqual(p.source, "workday")
        self.assertEqual(p.company, "Acme")
        self.assertEqual(p.location, "Boston, MA (Main Campus); Burlington, MA")
        self.assertEqual(p.url, "https://acme.wd1.myworkdayjobs.com/careers/job/Boston-MA-Main-Campus/Associate-Data-Scientist_R100001")
        self.assertEqual(p.posted_at, datetime(2026, 10, 7, tzinfo=timezone.utc))
        self.assertEqual((p.salary_min, p.salary_max), (76335.0, 107823.0))
        self.assertEqual(p.employment_type, "Full time")
        self.assertIs(p.remote, False)  # "Hybrid"
        self.assertIn("Python and SQL", p.text)
        self.assertNotIn("<p>", p.text)

    def test_only_titles_the_scorer_could_keep_get_a_detail_request(self):
        self.assertEqual(self.tenant.details, ["/job/Boston-MA-Main-Campus/Associate-Data-Scientist_R100001"])
        # Senior, foreign and postdoc titles still come back, from the search result alone.
        for title in ("Senior Data Engineer", "Machine Learning Engineer", "Postdoctoral Research Associate"):
            with self.subTest(title=title):
                self.assertEqual(self.posts[title].text, "")
        self.assertEqual(self.posts["Machine Learning Engineer"].location, "Toronto, ON, Canada")

    def test_titles_outside_the_role_families_are_dropped(self):
        self.assertNotIn("Financial Aid Counselor", self.posts)

    def test_country_filter_and_repeat_searches(self):
        first, *rest = self.tenant.searches
        self.assertEqual(first["appliedFacets"], {})
        self.assertEqual(first["searchText"], "data")
        self.assertEqual(rest[0]["appliedFacets"], {"locationCountry": [boards.WORKDAY_US]})
        self.assertTrue(all(s["appliedFacets"] == rest[0]["appliedFacets"] for s in rest))
        self.assertTrue(all(s["limit"] == 20 for s in self.tenant.searches))
        # The fixture fits on one page, and every later search only repeats it: one request each.
        self.assertEqual([s["searchText"] for s in rest], list(boards.WORKDAY_SEARCHES))


class RobotsTest(unittest.TestCase):
    def test_disallowed_api_skips_the_tenant(self):
        tenant = FakeTenant(robots="User-agent: *\nDisallow: /wday/\n")
        with self.assertRaises(boards.RobotsDisallowed):
            tenant.run()
        self.assertEqual(tenant.searches, [])

    def test_disallowed_job_site_skips_the_tenant(self):
        tenant = FakeTenant(robots="User-agent: *\nDisallow: /Careers/\n")  # site names are case-insensitive
        with self.assertRaises(boards.RobotsDisallowed):
            tenant.run()

    def test_missing_robots_allows(self):
        tenant = FakeTenant(robots_status=404)
        self.assertTrue(tenant.run())

    def test_forbidden_robots_disallows(self):
        with self.assertRaises(boards.RobotsDisallowed):
            FakeTenant(robots_status=403).run()


class RetryTest(unittest.TestCase):
    def fetch(self, *responses):
        queue = list(responses)
        with mock.patch.object(boards, "MIN_INTERVAL_S", 0), mock.patch.object(boards.time, "sleep") as sleep, \
                mock.patch.object(boards.requests, "get", lambda url, **_: queue.pop(0)):
            try:
                return boards._get_json("https://acme.example/jobs")
            finally:
                self.slept = [c.args[0] for c in sleep.call_args_list]

    def test_server_error_gets_one_retry(self):
        self.assertEqual(self.fetch(FakeResponse(502), FakeResponse(payload={"ok": True})), {"ok": True})
        self.assertEqual(self.slept, [10])

    def test_second_failure_raises(self):
        with self.assertRaises(requests.HTTPError):
            self.fetch(FakeResponse(429), FakeResponse(429))

    def test_client_errors_are_not_retried(self):
        with self.assertRaises(requests.HTTPError):
            self.fetch(FakeResponse(403))
        self.assertEqual(self.slept, [])


class HelpersTest(unittest.TestCase):
    def test_posted_on(self):
        now = datetime.now(timezone.utc)
        for text, days in (("Posted Today", 0), ("Posted Yesterday", 1), ("Posted 3 Days Ago", 3), ("Posted 30+ Days Ago", 31)):
            with self.subTest(text=text):
                self.assertAlmostEqual((now - boards._posted_on(text)) / timedelta(days=1), days, places=2)
        self.assertIsNone(boards._posted_on("Posted some time ago"))

    def test_host_for(self):
        self.assertEqual(boards.host_for("workday", "acme/wd1/careers"), HOST)
        self.assertEqual(boards.host_for("greenhouse", "anthropic"), "greenhouse")

    def test_date_only_values_are_utc(self):
        self.assertEqual(boards._dt("2026-10-07"), datetime(2026, 10, 7, tzinfo=timezone.utc))

    def test_places_become_city_and_state(self):
        for place, expected in (("Spring, Texas, United States of America", "Spring, TX"), ("Denver, CO, USA", "Denver, CO"),
                                ("US, CA, Santa Clara", "Santa Clara, CA"), ("USA-Colorado-Fort Collins", "Fort Collins, CO"),
                                ("IL-CHICAGO, 233 S WACKER DR, STE 3700", "Chicago, IL"), ("PA - Pittsburgh (15222)", "Pittsburgh, PA"),
                                ("Boston-MA", "Boston, MA"), ("Carmel IN", "Carmel, IN"), ("McLean, VA", "McLean, VA"),
                                ("AMER - United States - Colorado - Denver - Wewatta", "Denver, CO"),
                                ("Washington, District of Columbia", "Washington, DC")):
            with self.subTest(place=place):
                self.assertEqual(boards._city_state(place), expected)
        for place in ("Boston, MA (Main Campus)", "Massachusetts, USA - Remote", "Toronto, ON", "London, UK", "2 Locations",
                      "Porto Alegre, Rio Grande do Sul, Brazil", "Work At Home-Texas", "Lehi"):
            with self.subTest(place=place):
                self.assertEqual(boards._city_state(place), place)
        self.assertEqual(len(boards._STATE_CODE), 51)


class WorkableTest(unittest.TestCase):
    def test_workable_postings(self):
        from unittest import mock
        from shortlist import boards
        data = {"name": "Acme", "jobs": [
            {"title": "Data Engineer", "shortcode": "AB12", "url": "https://apply.workable.com/j/AB12", "country": "United States",
             "city": "Boston", "state": "Massachusetts", "published_on": "2026-10-14", "telecommuting": False,
             "description": "<p>Build ETL in Python. $90,000 - $110,000</p>", "employment_type": "Full-time", "department": "Data"},
            {"title": "ML Engineer", "shortcode": "CD34", "country": "France", "city": "Paris", "state": "", "published_on": "2026-10-01",
             "telecommuting": True, "description": "<p>ML</p>"}]}
        with mock.patch.object(boards, "_robots_allow", return_value=True), mock.patch.object(boards, "_get_json", return_value=data):
            boards._workable_robots.clear()
            ps = boards.workable("acme", "Acme")
        self.assertEqual(ps[0].location, "Boston, MA")
        self.assertEqual(ps[0].salary_min, 90000)
        self.assertEqual(ps[0].posted_at.date().isoformat(), "2026-10-14")
        self.assertIn("France", ps[1].location)
        self.assertEqual(ps[1].url, "https://apply.workable.com/acme/j/CD34")


if __name__ == "__main__":
    unittest.main()
