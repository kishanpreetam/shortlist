import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shortlist import apply, rewrite
from shortlist import resume as engine

MINE = "Built ETL pipelines in Python and SQL with Airflow for 1M+ rows."
FACTS = {
    "contact": {"name": "Sam Example", "lines": ["Boston, MA"], "links": [], "status": "approved"},
    "titles": {"items": ["Data Engineer"], "status": "approved"},
    "summaries": [{"id": "s1", "variant": "data-engineer", "text": "Data engineer who builds ETL pipelines.", "status": "approved"}],
    "education": [{"school": "State University", "degree": "M.S. Data Science", "date": "2025", "status": "approved"}],
    "skills": [{"id": "sk", "label": "Data", "items": ["ETL", "Airflow", "SQL"], "status": "approved"}],
    "roles": [{"id": "r1", "title": "Data Engineer", "org": "Acme", "place": "", "dates": "2023 – 2024", "status": "approved",
               "tools": ["Python", "SQL", "Airflow", "Spark"], "bullets": [
                   {"id": "r1a", "text": MINE, "status": "approved"},
                   {"id": "r1b", "text": "Built Tableau dashboards for 500 users.", "status": "approved"}]}],
    "projects": [],
    "variants": {"data-engineer": ["r1a"]},
}
P = engine.sentence
JD = "We need a data engineer with SQL, Airflow and Spark to build ETL pipelines and dashboards. " * 4


class TailorTest(unittest.TestCase):
    """The Tailor button: Claude's rewrites reach the preview only where they keep your facts."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "facts.json").write_text(json.dumps(FACTS))
        self.profile = {"resume_facts": str(self.tmp / "facts.json"), "claude_path": "/fake/claude"}
        for p in (mock.patch.object(engine, "_print_pdf", lambda html, wd, name, chrome: self.tmp / name),  # no Chrome
                  mock.patch.object(engine, "_pages", lambda pdf: 1),
                  mock.patch.object(apply, "RESULTS", self.tmp)):
            p.start()
            self.addCleanup(p.stop)

    def preview(self, answer=None, error=None, **extra):
        def fake(points, title, jd, claude, cache_dir, **kw):
            if error:
                raise rewrite.RewriteError(error)
            return answer
        with mock.patch.object(rewrite, "rewrite", fake):
            return apply.resume_preview({"paste": {"company": "Beta", "title": "Data Engineer", "jd": JD}, **extra}, self.profile)

    @staticmethod
    def bullets(r):
        return {b["id"]: b for g in r["experience"] for b in g["bullets"]}

    def test_rewrites_that_keep_the_facts_are_used(self):
        good = "Engineered Airflow ETL pipelines in Python and SQL to process 1M+ rows."
        r = self.preview({"r1a": good, "r1b": "Built Tableau dashboards for 5,000 users."})  # 5,000: a changed number
        b = self.bullets(r)
        self.assertEqual((b["r1a"]["text"], b["r1a"]["version"]), (P(good), "job"))  # shown as it prints
        self.assertEqual(b["r1b"]["text"], "Built Tableau dashboards for 500 users.")
        self.assertEqual((r["rewritten"], r["rejected"], r["rewrite_note"]), (1, 1, ""))
        self.assertEqual(r["originals"], {"r1a": P(MINE)})
        self.assertTrue(r["ok"])
        mine = self.preview({"r1a": good}, original=["r1a"])  # "my wording"
        self.assertEqual(self.bullets(mine)["r1a"]["text"], P(MINE))

    def test_signed_out_keeps_your_wording_and_says_why(self):
        r = self.preview(error="Claude Code isn't signed in. Run `claude auth login` in Terminal once.")
        self.assertIn("claude auth login", r["rewrite_note"])
        self.assertEqual((r["rewritten"], self.bullets(r)["r1a"]["text"], r["rewrite_failed"]), (0, P(MINE), True))
        self.assertTrue(r["ok"])

    def test_a_partial_answer_says_so(self):
        got = rewrite.Rewrites({"r1a": "Engineered Airflow ETL pipelines in Python and SQL to process 1M+ rows."})
        got.note = "Claude couldn't rewrite some of your points (timeout), so those keep your wording."
        r = self.preview(got)
        self.assertEqual((r["rewritten"], r["rewrite_failed"]), (1, False))
        self.assertIn("some of your points", r["rewrite_note"])

    def test_saving_keeps_the_resume_when_the_phone_copy_is_blocked(self):
        r = self.preview({})
        Path(apply.PREVIEWS[r["id"]]["pdf"]).write_bytes(b"%PDF-1.4")
        phone = self.tmp / "Library" / "Mobile Documents" / "Shortlist"
        self.profile.update(tailored_dir=str(self.tmp / "Documents"), tailored_copy_dir=str(phone))
        out = apply.resume_save({"id": r["id"]}, self.profile)
        self.assertEqual(out["note"], "")
        self.assertEqual(len(list(phone.rglob("*.pdf"))), 1)  # both copies when nothing blocks them
        real = shutil.copy2
        def blocked(src, dst, **kw):  # macOS refusing iCloud Drive to this app
            if "Mobile Documents" in str(dst):
                raise PermissionError(1, "Operation not permitted", str(dst))
            return real(src, dst, **kw)
        with mock.patch.object(engine.shutil, "copy2", blocked):
            out = apply.resume_save({"id": r["id"]}, self.profile)
        self.assertTrue(Path(out["path"]).exists())  # still saved
        self.assertIn("iCloud Drive", out["note"])
        self.assertIn("Privacy & Security", out["note"])

    def test_skill_answers_are_kept_with_where_and_a_no_is_not_asked_again(self):
        self.assertEqual(self.preview({})["places"], [{"id": "r1", "name": "Acme"}])  # for "where did you use it?"
        f = apply.facts_update({"section": "added_skill", "term": "kafka", "level": "used", "where": ["r1", "nope"]}, self.profile)
        entry = next(a for a in f["added_skills"] if a["term"] == "kafka")
        self.assertEqual((entry["level"], entry["where"]), ("used", ["r1"]))  # only your own roles and projects
        facts = lambda: engine.load_facts(self.tmp / "facts.json")
        self.assertIn("kafka", engine.mapped_tools(facts(), "r1"))  # rewrites for later jobs may name it in that role
        jd = JD + " Terraform and Kafka are required."
        state = lambda: {d["term"]: d["state"] for d in engine.select(facts(), jd, "Data Engineer", "data-engineer")["skills_detail"]}
        self.assertEqual(state()["terraform"], "missing")  # asked
        apply.facts_update({"section": "added_skill", "term": "terraform", "level": "no"}, self.profile)
        self.assertEqual(state()["terraform"], "declined")  # a no isn't asked again, and never reaches the page
        self.assertNotIn("Terraform", str(engine.select(facts(), jd, "Data Engineer", "data-engineer")["skills"]))
        apply.facts_update({"section": "added_skill", "term": "terraform", "level": "removed"}, self.profile)
        self.assertEqual(state()["terraform"], "missing")  # "ask again"
        with self.assertRaises(ValueError):
            apply.facts_update({"section": "added_skill", "term": "terraform", "level": "maybe"}, self.profile)

    def test_suggested_lines_are_labeled_and_one_is_recommended(self):
        drafts = [{"place": "r1", "text": "Built Spark streaming for 9M+ rows.", "basis": [], "needs_you": False, "recommended": True, "why": "bold"},
                  {"place": "r1", "text": "Built ETL pipelines in Python and SQL with Airflow for 1M+ rows.", "basis": ["r1a"],
                   "needs_you": False, "recommended": False, "why": ""},
                  {"place": "r1", "text": "Built pipelines; fast.", "basis": [], "needs_you": False, "recommended": False, "why": ""}]
        with mock.patch.object(rewrite, "suggest_lines", lambda *a, **k: drafts):
            r = apply.resume_suggest({"paste": {"company": "Beta", "title": "Data Engineer", "jd": JD}, "requirement": "Build ETL pipelines"}, self.profile)
        self.assertEqual(len(r["suggestions"]), 2)  # the one with a semicolon is dropped
        first, second = r["suggestions"]
        self.assertEqual((first["recommended"], first["grounded"], first["place"]), (True, True, "r1"))  # from your lines beats a bold claim
        self.assertFalse(second["grounded"])
        self.assertTrue(any("9M+" in a for a in second["adds"]))
        with self.assertRaises(ValueError):  # a line with unfilled blanks can't be saved
            apply.facts_update({"section": "new_line", "group": "r1", "text": "Built a [model] in PyTorch for the team."}, self.profile)

    def test_the_recommendation_names_the_skill_adds_what_the_page_lacks_and_stays_true(self):
        page = "Built ETL pipelines in Python and SQL with Airflow for 1M+ rows."
        both = "Built Airflow ETL pipelines in Python and SQL for 1M+ rows and Tableau dashboards for 500 users."
        drafts = [{"place": "r1", "text": page, "basis": ["r1a"], "needs_you": False, "recommended": True, "why": ""},  # already on the page
                  {"place": "r1", "text": "Built Tableau dashboards for 500 users.", "basis": ["r1b"], "needs_you": False,
                   "recommended": False, "why": ""},  # true, but says nothing of the requirement
                  {"place": "r1", "text": both, "basis": ["r1a", "r1b"], "needs_you": False, "recommended": False, "why": ""},
                  {"place": "r1", "text": "Built Airflow DAGs with Kafka for 9M+ rows.", "basis": [], "needs_you": True, "recommended": False, "why": ""}]
        with mock.patch.object(rewrite, "suggest_lines", lambda *a, **k: drafts):
            r = apply.resume_suggest({"paste": {"company": "Beta", "title": "Data Engineer", "jd": JD},
                                      "requirement": "Experience orchestrating ETL pipelines with Airflow", "on_page": ["r1a"]}, self.profile)
        texts = [x["text"] for x in r["suggestions"]]
        self.assertNotIn(page, texts)  # the page shows it already
        best = r["suggestions"][0]
        self.assertEqual((best["text"], best["recommended"], best["grounded"], best["rank"]["named"]), (both, True, True, 1.0))
        self.assertIn("covers", best["why"])
        self.assertEqual(sum(x["strongest"] for x in r["suggestions"]), 1)  # always exactly one strongest
        self.assertEqual(texts[-1], "Built Tableau dashboards for 500 users.")  # addresses none of it: last
        self.assertFalse(r["suggestions"][1]["grounded"])  # a relevant claim you'd confirm sits between them
        self.assertEqual(r["suggestions"][1]["truth"], 2)  # it adds a number only you can vouch for

    def test_an_added_line_comes_back_to_pin_and_a_near_copy_is_not_added_twice(self):
        f = apply.facts_update({"section": "new_line", "group": "r1", "text": "Scheduled Airflow DAGs for nightly loads of sales data."}, self.profile)
        self.assertTrue(f["added"].startswith("r1_you"))
        n = len(json.loads((self.tmp / "facts.json").read_text())["roles"][0]["bullets"])
        again = apply.facts_update({"section": "new_line", "group": "r1", "text": "Scheduled Airflow DAGs for the nightly loads of sales data."}, self.profile)
        self.assertEqual(again["added"], f["added"])  # nearly the same line: the one you have is pinned instead
        self.assertEqual(len(json.loads((self.tmp / "facts.json").read_text())["roles"][0]["bullets"]), n)
        r = self.preview({}, include=[f["added"]])
        self.assertIn(f["added"], self.bullets(r))  # pinned to this resume

    def test_fitting_starts_from_the_size_that_fit_last_time(self):
        prints, pages = [], iter([2, 1])
        with mock.patch.object(engine, "_print_pdf", lambda html, wd, name, chrome: prints.append(1) or self.tmp / name), \
                mock.patch.object(engine, "_pages", lambda pdf: next(pages)), mock.patch.dict(engine._FIT, {"index": 3}):
            out = engine.build(FACTS, JD, "Data Engineer", "data-engineer", self.tmp, None, "chrome")
            self.assertEqual((out["pages"], len(prints), engine._FIT["index"]), (1, 2, 3))  # one step fuller ran over, the last size fit

    def test_turned_off_never_calls_claude(self):
        self.profile["rewrite_points"] = False
        r = self.preview(error="should not be called")
        self.assertEqual((r["rewrite_note"], r["rewritten"]), ("", 0))


class WarmUpTest(unittest.TestCase):
    """The background warm-up rewrites only the next few jobs you haven't applied to or skipped."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "facts.json").write_text(json.dumps(FACTS))
        items = [{"n": i, "company": f"Co{i}", "title": "Data Engineer", "url": f"https://example.com/{i}"} for i in range(1, 9)]
        (self.tmp / "today.json").write_text(json.dumps({"items": items}))
        self.asked = []
        for p in (mock.patch.object(apply, "TODAY", self.tmp / "today.json"), mock.patch.object(apply, "RESULTS", self.tmp),
                  mock.patch.object(apply, "posting_text", lambda url: JD),
                  mock.patch.object(apply, "_state", lambda profile: {"applied": {"https://example.com/1": {}},
                                                                      "skipped": ["https://example.com/3"]}),
                  mock.patch.object(rewrite, "claude_path", lambda profile=None: "/fake/claude")):
            p.start()
            self.addCleanup(p.stop)

    def warm(self, profile, fail_on=None):
        def fake(points, title, jd, claude, cache_dir, parallel=True, **kw):
            self.asked.append((points[0]["id"] if points else None, parallel))
            if len(self.asked) == fail_on:
                raise rewrite.RewriteError("Claude Code isn't signed in.")
            return rewrite.Rewrites()
        with mock.patch.object(rewrite, "rewrite", fake):
            apply.kick_warmup()
            apply.warm_rewrites({"resume_facts": str(self.tmp / "facts.json"), **profile}, rounds=1)

    def test_next_five_jobs_one_call_at_a_time(self):
        self.warm({})
        self.assertEqual(len(self.asked), 5)  # jobs 2, 4, 5, 6 and 7: 1 is applied and 3 is skipped
        self.assertTrue(all(parallel is False for _, parallel in self.asked))

    def test_stops_at_the_first_error_and_can_be_turned_off(self):
        self.warm({}, fail_on=2)
        self.assertEqual(len(self.asked), 2)
        self.assertIn("signed in", apply.WARM["note"])
        self.asked.clear()
        self.warm({"rewrite_warmup": 0})
        self.warm({"rewrite_warmup": False})
        self.assertEqual(self.asked, [])
        self.warm({"rewrite_warmup": 2})
        self.assertEqual(len(self.asked), 2)


if __name__ == "__main__":
    unittest.main()
