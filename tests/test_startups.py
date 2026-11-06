import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import yaml

from shortlist import startups

TODAY = date(2026, 10, 29)


class StartupRule(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "facts.yaml").write_text(yaml.safe_dump({
            "Tiny New": {"founded": 2023, "employees": 40, "source": "test"},
            "Ten Years": {"founded": 2016, "employees": 500, "source": "test"},
            "Eleven Years": {"founded": 2015, "employees": 120, "source": "test"},
            "Young Big": {"founded": 2021, "employees": 501, "source": "test"},
            "Half Known": {"founded": 2022, "employees": None, "source": "test"},
            "Listed Old": {"founded": 2022, "employees": 30, "source": "test"},
        }))
        (self.tmp / "established.yaml").write_text(yaml.safe_dump(["Listed Old"]))
        self.patches = [mock.patch.object(startups, "FACTS_FILE", self.tmp / "facts.yaml"),
                        mock.patch.object(startups, "ESTABLISHED_FILE", self.tmp / "established.yaml"),
                        mock.patch.dict(startups._cache, clear=True),
                        mock.patch.dict(startups._limits, {"years": 10, "employees": 500})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def test_young_and_small_only(self):
        got = {c: startups.is_startup(c, "Greenhouse", TODAY) for c in
               ("Tiny New", "Ten Years", "Eleven Years", "Young Big", "Half Known", "Listed Old", "Never Heard Of")}
        self.assertEqual(got, {"Tiny New": True, "Ten Years": True, "Eleven Years": False, "Young Big": False,
                               "Half Known": False, "Listed Old": False, "Never Heard Of": False})

    def test_workday_is_never_a_startup(self):
        self.assertFalse(startups.is_startup("Tiny New", "Workday", TODAY))

    def test_reasons(self):
        self.assertEqual(startups.why("Tiny New", "Ashby", TODAY), (True, "founded 2023, about 40 people"))
        self.assertEqual(startups.why("Eleven Years", "Ashby", TODAY), (False, "founded 2015, about 120 people"))
        self.assertEqual(startups.why("Never Heard Of", "Ashby", TODAY)[1], "founding year or headcount not known yet")

    def test_limits_from_the_profile(self):
        startups.configure({"startup_max_years": 5, "startup_max_employees": 100})
        self.assertEqual([startups.is_startup(c, "Lever", TODAY) for c in ("Tiny New", "Ten Years")], [True, False])
        startups.configure({})
        self.assertTrue(startups.is_startup("Ten Years", "Lever", TODAY))

    def test_missing_lists_unknown_employers(self):
        self.assertEqual(startups.missing(["Tiny New", "Half Known", "Listed Old", "Never Heard Of"]),
                         ["Half Known", "Never Heard Of"])


class RealFacts(unittest.TestCase):
    """The checked-in facts file: well formed, and the employers the user named aren't startups."""

    def setUp(self):
        startups._cache.clear()

    def test_well_formed(self):
        for name, f in startups.facts().items():
            with self.subTest(name=name):
                self.assertTrue(f.get("founded") is None or isinstance(f["founded"], int))
                self.assertTrue(f.get("employees") is None or isinstance(f["employees"], int))
                self.assertTrue(str(f.get("source") or "").strip())

    def test_openai_and_notion_are_not_startups(self):
        if not startups.FACTS_FILE.exists():
            self.skipTest("no facts file yet")
        for name in ("OpenAI", "Notion"):
            self.assertFalse(startups.is_startup(name, "Ashby", TODAY), name)


if __name__ == "__main__":
    unittest.main()
