import unittest
from datetime import date
from unittest import mock

from shortlist import apply
from shortlist.apply import add_business_days, band, field_of, pick, title_key


def posting(company, title, score, age, url=None, excluded=""):
    return {"company": company, "title": title, "score": str(score), "age_days": str(age),
            "url": url or f"https://example.com/{company}/{title_key(title).replace(' ', '-')}/{age}",
            "excluded": excluded, "role": "data_engineer"}


class PickTest(unittest.TestCase):
    today = date(2026, 10, 12)

    def test_score_weighted_by_freshness(self):
        rows = [posting("A", "Data Engineer", 40, 20), posting("B", "Data Engineer", 10, 1),
                posting("C", "Data Engineer", 30, 2)]  # weighted: A 24, B 20, C 60
        self.assertEqual([r["company"] for r in pick(rows, [], [], today=self.today)], ["C", "A", "B"])

    def test_senior_titles_the_classifier_misses(self):
        rows = [posting("A", "Leader, Solutions Engineer", 30, 1), posting("B", "Principle Robotics Software Engineer", 30, 1),
                posting("C", "Systems Analyst Advisor", 30, 1), posting("D", "Data Engineer 5", 30, 1),
                posting("E", "Data Engineer 1", 30, 1)]
        self.assertEqual([r["company"] for r in pick(rows, [], [], today=self.today)], ["E"])

    def test_age_offset_and_title_exclusions(self):
        rows = [posting("A", "Data Engineer", 30, 59), posting("B", "IT Systems Engineer, macOS", 30, 1),
                posting("C", "Software Engineer, Agent (Spanish speaking)", 30, 1)]
        self.assertEqual(pick(rows, [], [], today=self.today, age_offset=2), [])

    def test_cap_per_employer_counts_recent_applications(self):
        rows = [posting("A", f"Data Engineer {i}", 50 - i, 1) for i in range(5)]
        applied = [{"company": "A", "title": "Old role", "url": "u", "applied_on": "2026-10-01"}]
        self.assertEqual(len(pick(rows, applied, [], cap=3, today=self.today)), 2)

    def test_one_per_title_across_locations(self):
        rows = [posting("A", "Software Engineer, Early Career (NYC)", 30, 1),
                posting("A", "Software Engineer, Early Career (SF)", 29, 1)]
        self.assertEqual(len(pick(rows, [], [], today=self.today)), 1)

    def test_skips_excluded_applied_skipped_and_stale(self):
        done = posting("A", "Data Engineer", 30, 1)
        rows = [done, posting("B", "Data Engineer", 30, 1, excluded="senior title"),
                posting("C", "Data Engineer", 30, 90), posting("D", "Data Engineer", 30, 1)]
        skipped = [{"url": rows[3]["url"]}]
        applied = [{"company": "A", "title": "Data Engineer", "url": done["url"], "applied_on": "2026-10-12"}]
        self.assertEqual(pick(rows, applied, skipped, today=self.today), [])

    def test_startups_come_first_then_established_employers(self):
        rows = [posting("Bigco", "Data Engineer", 60, 1), posting("Seed", "Data Engineer", 20, 1),
                posting("Ent", "Data Engineer", 50, 1, url="https://ent.wd1.myworkdayjobs.com/jobs/job/1"),
                posting("Series B", "Data Engineer", 30, 1)]
        with mock.patch.object(apply, "_established", {"Bigco"}):
            self.assertEqual([r["company"] for r in pick(rows, [], [], today=self.today)], ["Series B", "Seed", "Bigco", "Ent"])
            self.assertEqual([r["company"] for r in pick(rows, [], [], n=2, today=self.today)], ["Series B", "Seed"])

    def test_workday_pages_on_myworkdaysite_count_as_workday(self):
        url = "https://wd5.myworkdaysite.com/recruiting/guidewire/external/job/United-States/AI-Engineer_JR1"
        self.assertEqual(apply.board_of(url), "Workday")
        rows = [posting("Guidewire", "AI Engineer", 50, 1, url=url), posting("Seed", "Data Engineer", 20, 1)]
        with mock.patch.object(apply, "_established", set()):
            self.assertEqual([r["company"] for r in pick(rows, [], [], today=self.today)], ["Seed", "Guidewire"])


class HelpersTest(unittest.TestCase):
    def test_business_days_skip_weekends(self):
        self.assertEqual(add_business_days(date(2026, 10, 9), 4), date(2026, 10, 15))  # Fri + 4 = Thu

    def test_bands(self):
        self.assertEqual([band(a) for a in (0, 3, 4, 14, 15, 30, 31, 60, 61)], [0, 0, 1, 1, 2, 2, 3, 3, 4])

    def test_title_key_ignores_parentheses_and_punctuation(self):
        self.assertEqual(title_key("Software Engineer, New Grad (2027)"), "software engineer new grad")


class FieldTest(unittest.TestCase):
    def test_fields(self):
        f = field_of
        with mock.patch.object(apply, "_established", {"Acme", "Robinhood", "Okta"}):
            self.assertEqual(f("Cursor", "Software Engineer", "", "Ashby"), "startup")
            self.assertEqual(f("Ambience Healthcare Inc", "Data Engineer", "patients clinical HIPAA patients", "Ashby"), "startup")
            self.assertEqual(f("Okta", "Software Engineer", "", "Greenhouse"), "big")
            self.assertEqual(f("Intel", "Software Engineer", "", "Workday"), "big")
            self.assertEqual(f("University of Chicago", "Business Analyst", "", "Workday"), "big")  # no cap-exempt group
            self.assertEqual(f("Elevance Health", "Business Analyst", "", "Workday"), "healthcare")
            self.assertEqual(f("Acme", "Data Analyst", "patients clinical HIPAA patients clinical", "Greenhouse"), "healthcare")
            self.assertEqual(f("Robinhood", "Software Engineer", "", "Greenhouse"), "finance")

    def test_the_established_list_is_plain_unique_names(self):
        import yaml
        listed = yaml.safe_load(apply.ESTABLISHED_FILE.read_text())
        self.assertTrue(all(isinstance(n, str) and n == n.strip() for n in listed))
        self.assertEqual(len(listed), len(set(listed)))
        self.assertTrue({"HubSpot", "Toast", "Wayfair"} <= set(listed))
        self.assertFalse({"Anthropic", "Cursor", "OpenAI"} & set(listed))


class ExcludeTest(unittest.TestCase):
    def test_lab_science_and_business_development_titles(self):
        import re
        from shortlist.apply import EXCLUDE_TITLES
        rx = re.compile("|".join(f"(?:{p})" for p in EXCLUDE_TITLES), re.I)
        for title in ("Research Scientist I/II: Pharmacology", "Market Consultant BD", "Business Development Analyst"):
            self.assertTrue(rx.search(title), title)
        for title in ("Bioinformatics / Omics Data Expert", "Data Engineer", "Analytics Engineer"):
            self.assertFalse(rx.search(title), title)


class FreshTest(unittest.TestCase):
    def test_hours_old_and_the_24_hour_window(self):
        from datetime import datetime, timedelta, timezone
        from shortlist.apply import hours_old
        now = datetime.now(timezone.utc)
        fresh = posting("A", "Data Engineer", 40, 0) | {"posted_at": (now - timedelta(hours=5)).isoformat()}
        old = posting("B", "Data Engineer", 40, 1) | {"posted_at": (now - timedelta(hours=30)).isoformat()}
        undated = posting("C", "Data Engineer", 40, 0)
        self.assertLess(abs(hours_old(fresh) - 5), 0.1)
        picked = [r["company"] for r in pick([fresh, old, undated], [], [], max_age_hours=24)]
        self.assertEqual(sorted(picked), ["A", "C"])  # C has no time, but its board says today

    def test_a_date_without_a_time_counts_from_noon_eastern(self):
        from datetime import datetime, timedelta, timezone
        from shortlist.apply import date_only, hours_old
        day = datetime(2026, 10, 14, tzinfo=timezone.utc)
        r = posting("W", "Data Engineer", 40, 0) | {"posted_at": day.isoformat()}
        self.assertTrue(date_only(r))
        self.assertAlmostEqual(hours_old(r, now=day + timedelta(hours=36)), 20.0)  # 8 a.m. Eastern the next day

    def test_search_links_are_last_24_hours(self):
        from shortlist.apply import search_links
        role, links = search_links(["Data Engineer"])[0]
        urls = dict(links)
        self.assertIn("f_TPR=r86400", urls["LinkedIn"])
        self.assertIn("fromage=1", urls["Indeed"])


if __name__ == "__main__":
    unittest.main()
