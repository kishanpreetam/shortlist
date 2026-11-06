import csv
import io
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from shortlist import apply, lists, startups

FIELDS = ["score", "company", "title", "role", "location", "salary", "salary_source", "wage_level", "lottery_odds",
          "sponsor", "sponsor_reason", "fit", "matched", "missing", "years_required", "age_days", "leverage", "excluded",
          "url", "posted_at"]


def row(company, title, hours_ago, score=30, url=None, **kw):
    t = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {"score": str(score), "company": company, "title": title, "role": "data_engineer", "location": "Boston, MA",
            "salary": "120000", "salary_source": "posted", "wage_level": "2", "lottery_odds": "0.52", "sponsor": "0.9",
            "sponsor_reason": "40 H-1B filings for this role family", "fit": "0.7", "matched": "python sql",
            "missing": "", "years_required": "2", "age_days": str(int(hours_ago // 24)), "leverage": "", "excluded": "",
            "url": url or f"https://job-boards.greenhouse.io/{company.lower().replace(' ', '')}/jobs/{abs(hash(title)) % 10**8}",
            "posted_at": t.isoformat(), **kw}


class Lists(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.profile = {"roles": ["data_engineer"], "years_experience": 4}
        self.patches = [mock.patch.object(apply, "POSTINGS", self.tmp / "postings.csv"),
                        mock.patch.object(apply, "APPS", self.tmp / "applications.csv"),
                        mock.patch.object(apply, "SKIPS", self.tmp / "skipped.csv"),
                        mock.patch.object(lists, "LISTS", self.tmp / "lists"),
                        mock.patch.object(apply, "posting_text", lambda url: "About the role. " * 30),
                        mock.patch.object(startups, "facts", lambda: {"Seed": {"founded": 2023, "employees": 40, "source": "t"}}),
                        mock.patch.object(startups, "established", lambda: {"Bigco"})]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def write(self, rows):
        with (self.tmp / "postings.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, FIELDS)
            w.writeheader()
            w.writerows(rows)

    def test_keeps_the_last_24_hours_that_fit_startups_first(self):
        self.write([row("Bigco", "Data Engineer", 3, score=60), row("Seed", "Data Engineer", 5, score=20),
                    row("Old Co", "Analytics Engineer", 40), row("Gone", "Data Engineer", 2, excluded="senior title"),
                    row("Bigco", "Senior Data Engineer II", 1, excluded="senior title")])
        doc, new = lists.make(self.profile)
        self.assertEqual([(i["n"], i["company"]) for i in doc["items"]], [(1, "Seed"), (2, "Bigco")])
        self.assertEqual(new, 2)
        self.assertEqual([i["field"] for i in doc["items"]], ["startup", "big"])
        self.assertTrue(all(len(i["text"]) >= 200 for i in doc["items"]))  # kept for Tailor, for later

    def test_numbered_in_reading_order(self):
        self.write([row("Bigco", "Data Engineer", 3, score=90), row("Seed", "Data Engineer", 5, score=10),
                    row("Hospital Health", "Data Analyst", 2, score=80), row("Bigco", "Data Scientist", 4, score=70)])
        doc, _ = lists.make(self.profile)
        self.assertEqual([(i["n"], i["company"], i["field"]) for i in doc["items"]],
                         [(1, "Seed", "startup"), (2, "Bigco", "big"), (3, "Bigco", "big"), (4, "Hospital Health", "healthcare")])

    def test_numbers_never_change_on_the_same_day(self):
        self.write([row("Seed", "Data Engineer", 5), row("Bigco", "Data Engineer", 3)])
        first, _ = lists.make(self.profile)
        self.write([row("Seed", "Data Engineer", 5), row("Bigco", "Data Engineer", 3),
                    row("Seed", "Machine Learning Engineer", 1, score=90), row("Newco", "Data Engineer", 1)])
        second, new = lists.make(self.profile)
        self.assertEqual([(i["n"], i["company"], i["title"]) for i in second["items"][:2]],
                         [(i["n"], i["company"], i["title"]) for i in first["items"]])
        self.assertEqual((new, [i["n"] for i in second["items"]]), (2, [1, 2, 3, 4]))

    def test_cap_and_one_per_title_count_what_is_listed(self):
        old = [{"n": 1, "company": "Acme", "title": "Data Engineer", "url": "u1"},
               {"n": 2, "company": "Acme", "title": "Data Analyst", "url": "u2"}]
        fresh = [{"company": "Acme", "title": "ML Engineer", "url": "u3"},
                 {"company": "Beta", "title": "Data Engineer", "url": "u4"},
                 {"company": "Beta", "title": "Data Engineer (Remote)", "url": "u5"},
                 {"company": "Acme", "title": "Data Engineer", "url": "u1"}]
        got = lists.merge(old, fresh, cap=2)
        self.assertEqual([(i["n"], i["url"]) for i in got], [(1, "u1"), (2, "u2"), (3, "u4")])

    def test_saved_files(self):
        self.write([row("Seed", "Data <Engineer>", 2, url="javascript:alert(1)"), row("Bigco", "Data Engineer", 3)])
        doc, _ = lists.make(self.profile)
        out = lists.folder(doc["date"])
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["jobs.csv", "jobs.html", "jobs.json"])
        page = (out / "jobs.html").read_text()
        self.assertNotIn("<script", page)
        self.assertIn("Data &lt;Engineer&gt;", page)
        self.assertNotIn('href="javascript', page)  # only web links become links
        self.assertIn('href="https://job-boards.greenhouse.io/bigco/', page)
        rows = list(csv.DictReader(io.StringIO((out / "jobs.csv").read_text())))
        self.assertEqual([r["n"] for r in rows], ["1", "2"])
        self.assertEqual(rows[0]["sponsorship"], "40 H-1B filings for this role family")
        self.assertEqual(json.loads((out / "jobs.json").read_text())["items"][1]["company"], "Bigco")
        self.assertEqual(lists.load()["date"], doc["date"])  # the latest list
        self.assertIsNone(lists.load("2001-01-01"))

    def test_a_missing_description_is_flagged(self):
        self.write([row("Seed", "Data Engineer", 2)])
        with mock.patch.object(apply, "posting_text", lambda url: ""):
            doc, _ = lists.make(self.profile)
        self.assertIn("The description wasn't saved", lists.page(doc))

    def test_hours_are_limited(self):
        self.write([row("Seed", "Data Engineer", 2)])
        self.assertEqual(lists.make(self.profile, hours=500)[0]["hours"], lists.MAX_HOURS)

    def test_how_facts_read(self):
        self.assertEqual(lists.posted({"posted_at": "2026-10-29T00:00:00", "date_only": True}), "Oct 29")
        self.assertEqual(lists.odds({"lottery": "1.0"}), "no lottery (cap-exempt employer)")
        self.assertEqual(lists.odds({"lottery": "0.52", "wage_level": "2"}), "52% at wage level 2")
        self.assertEqual(lists.pay({"salary": 93000, "salary_source": "typical for role"}), "about $93,000 (typical for the role)")
        self.assertEqual(lists.years({"years": "3", "have": 4}), "asks 3+ years (you have 4)")
        self.assertEqual(lists.years({"years": ""}), "no years asked")


if __name__ == "__main__":
    unittest.main()
