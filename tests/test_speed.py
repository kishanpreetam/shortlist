"""Tailor's speedups: new points written while Claude rewrites yours, the resume engine reading each text once
instead of thousands of times a build, and a page the fill loop tries again not printed twice. Each keeps what a
preview builds as it was. No Claude and no Chrome here."""

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tailor import app, rewrite
from tailor import resume as engine
from tests.test_lexicon import entry, lex_of
from tests.test_tailor import FACTS, JD, MINE, shortlist_files

REQS = [{"id": "R1", "text": "Build ETL pipelines in Python and SQL with Airflow", "kind": "required"},
        {"id": "R2", "text": "Experience building Tableau dashboards", "kind": "preferred"},
        {"id": "L1", "text": "Must be authorized to work in the US", "kind": "logistics"}]
LINES = REQS[:2]  # what lines can show: the logistics stay off


def answer(n: int) -> list[dict]:
    """The point engine's n-th answer in a test."""
    return [{"place": "r1", "text": f"Scheduled Spark jobs in Python with Airflow for nightly loads, run {n}.", "shows": ["R1"]}]


class PointsAheadTest(unittest.TestCase):
    """New points (generate_points in profile.yaml) are asked for while Claude rewrites your points, from the same
    read of the posting, and used only when they came from the requirements the rewrites came from."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "facts.json").write_text(json.dumps(FACTS))
        self.profile = {"resume_facts": str(self.tmp / "facts.json"), "claude_path": "/fake/claude", "generate_points": True}
        self.asked, self.used, self.generated = [], [], threading.Event()
        self.reqs, self.wait_s, self.fails, self.rewrite_error, self.overlapped = REQS, 0, 0, None, None
        real = app.with_generated

        def with_generated(facts, points, job_text="", rewritten=None):
            self.used.append(points)
            return real(facts, points, job_text, rewritten)
        for p in (mock.patch.object(engine, "_print_pdf", lambda html, wd, name, chrome: self.tmp / name),  # no Chrome
                  mock.patch.object(engine, "_pages", lambda pdf: 1), mock.patch.object(app, "RESULTS", self.tmp),
                  mock.patch.object(rewrite, "requirements", lambda *a: REQS), mock.patch.object(rewrite, "keywords", lambda *a: []),
                  mock.patch.object(rewrite, "generate_points", self.generate), mock.patch.object(rewrite, "rewrite", self.rewrite),
                  mock.patch.object(app, "with_generated", with_generated), *shortlist_files(self.tmp / "shortlist")):
            p.start()
            self.addCleanup(p.stop)

    def generate(self, title, jd, reqs, places, claude, cache_dir):  # stands in for Claude's point engine
        self.asked.append((title, jd, reqs, places, claude, cache_dir))
        self.generated.set()
        if len(self.asked) <= self.fails:
            raise rewrite.RewriteError("Claude didn't answer (TimeoutExpired).")
        return answer(len(self.asked))

    def rewrite(self, points, title, jd, claude, cache_dir, **kw):  # stands in for Claude's rewrites of your points
        self.overlapped = self.generated.wait(self.wait_s)  # whether new points were asked for before these came back
        if self.rewrite_error:
            raise rewrite.RewriteError(self.rewrite_error)
        out = rewrite.Rewrites({"r1a": MINE})
        out.requirements, out.keywords, out.shows, out.summary, out.repaired, out.note = self.reqs, [], {}, None, [], ""
        return out

    def preview(self, **d):
        return app.resume_preview({"paste": {"company": "Beta", "title": "Data Engineer", "jd": JD}, **d}, self.profile)

    def test_new_points_are_asked_for_before_the_rewrites_come_back(self):
        self.wait_s = 5
        r = self.preview()
        self.assertTrue(self.overlapped)
        places = app.places(engine.load_facts(self.tmp / "facts.json"))
        self.assertEqual(self.asked, [("Data Engineer", JD, LINES, places, "/fake/claude", self.tmp / "generated")])  # as before
        self.assertEqual(self.used, [answer(1)])
        self.assertNotIn("New points", r["rewrite_note"])

    def test_points_written_from_other_requirements_are_asked_for_again(self):
        self.reqs = [dict(REQS[0], text="Build ETL pipelines in Python"), REQS[2]]  # the rewrites read the posting anew
        self.preview()
        self.assertEqual([a[2] for a in self.asked], [LINES, self.reqs[:1]])
        self.assertEqual(self.used, [answer(2)])  # the answer for the requirements the rewrites came from

    def test_a_failed_early_call_is_asked_again(self):
        self.fails = 1
        r = self.preview()
        self.assertEqual((len(self.asked), self.used), (2, [answer(2)]))
        self.assertNotIn("New points", r["rewrite_note"])

    def test_failing_twice_says_so_and_adds_nothing(self):
        self.fails = 2
        r = self.preview()
        self.assertEqual((len(self.asked), self.used), (2, []))
        self.assertIn("New points couldn't be written for this job (Claude didn't answer (TimeoutExpired).)", r["rewrite_note"])

    def test_rewrites_that_failed_take_no_new_points(self):
        self.rewrite_error, self.wait_s = "Claude Code isn't signed in.", 5  # the early call is done before the test ends
        r = self.preview()
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(self.used, [])
        self.assertTrue(r["rewrite_failed"])

    def test_no_new_points_unless_asked_for(self):
        for extra, d in (({"generate_points": False}, {}), ({"rewrite_points": False}, {}), ({}, {"rewrite": False})):
            with self.subTest(profile=extra, choices=d):
                self.asked.clear()
                with mock.patch.dict(self.profile, extra):
                    self.preview(**d)
                self.assertEqual(self.asked, [])
        with mock.patch.object(rewrite, "claude_path", lambda profile=None: None):
            self.assertIsNone(app._points_ahead(FACTS, "Data Engineer", JD, self.profile))


class CachedReadsTest(unittest.TestCase):
    """The resume engine reads each text once for the tools it knows: the same answer as reading it afresh, a copy
    for each caller, and learning tools (use_lexicon) or setting never-words (never_words) starts over."""

    CACHES = ("_terms", "_claim_set", "_word_terms", "_term_set", "_match_in", "_rewrite_problems", "_labels")
    LINE = "Built order queues in RabbitMQ and Python for 2,000+ stores."

    def setUp(self):
        was, words = dict(engine.LEARNED), engine.never_list()
        self.addCleanup(engine.use_lexicon, {"terms": was} if was else None)
        self.addCleanup(engine.never_words, words)
        engine.use_lexicon(None)
        engine.never_words([])

    def test_a_cached_read_is_a_fresh_read(self):
        for text in (MINE, JD, self.LINE, "Built RAG agents with LangChain on AWS (S3, Lambda).", "", None):
            with self.subTest(text=text):
                self.assertEqual(engine.terms(text), dict(engine._terms.__wrapped__(text or "")))
                self.assertEqual(engine.terms(text), engine.terms(text))
        for text in (MINE, "real-time, 10-agent systems", "the team's data"):
            self.assertEqual(engine._compounds(text), list(engine._compound_words.__wrapped__(text)))
            self.assertEqual(engine._claims(text), set(engine._claim_set.__wrapped__(text)))
            self.assertEqual(engine._flat(text), engine._flat.__wrapped__(text))
        rewritten = "Built ETL pipelines with Airflow in Python and SQL for 1M+ rows in Snowflake."
        self.assertEqual(engine.rewrite_problems(MINE, rewritten, {"spark"}, (), ["ETL pipelines"]),
                         list(engine._rewrite_problems.__wrapped__(MINE, rewritten, frozenset({"spark"}), (), ("ETL pipelines",), "")))

    def test_each_caller_gets_its_own_copy(self):
        for read, add in ((lambda: engine.terms(MINE), lambda x: x.update(made_up=9.0)),
                          (lambda: engine._claims(MINE), lambda x: x.add("made_up")),
                          (lambda: engine._compounds(MINE), lambda x: x.append("made_up")),
                          (lambda: engine.rewrite_problems(MINE, MINE + " Also Rust."), lambda x: x.append("made_up"))):
            add(read())
            self.assertNotIn("made_up", read())

    def test_learning_tools_starts_every_read_over(self):
        self.assertNotIn("rabbitmq", engine.terms(self.LINE))
        engine.keyword_shown("RabbitMQ", [self.LINE]), engine._item_terms(["RabbitMQ"]), engine._word_terms("RabbitMQ")
        engine.rewrite_problems(self.LINE, self.LINE), engine._term_set(self.LINE), engine._claims(self.LINE)
        engine.use_lexicon(lex_of(rabbitmq=entry("RabbitMQ")))
        self.assertEqual({name: getattr(engine, name).cache_info().currsize for name in self.CACHES}, dict.fromkeys(self.CACHES, 0))
        self.assertIn("rabbitmq", engine.terms(self.LINE))
        self.assertIn("rabbitmq", engine._claims(self.LINE))
        self.assertEqual(engine._item_terms(["RabbitMQ"]), {"rabbitmq"})  # its label reads as the tool now

    def test_a_read_worked_out_while_the_tools_change_is_never_read_again(self):
        real = engine._matches

        def learn_midway(text):  # a learn in the background finishes while this text is being read
            got = real(text)
            engine.use_lexicon(lex_of(rabbitmq=entry("RabbitMQ")))
            return got
        with mock.patch.object(engine, "_matches", learn_midway):
            self.assertNotIn("rabbitmq", engine.terms(self.LINE))  # read with the tools known when it started
        self.assertIn("rabbitmq", engine.terms(self.LINE))  # read again for the tools known now

    def test_never_words_start_the_rewrite_checks_over(self):
        line, text = "Built ETL pipelines in Python as an intern at Acme.", "As an intern at Acme, built ETL pipelines in Python."
        never = "it says intern, which never goes on your resume"
        self.assertNotIn(never, engine.rewrite_problems(line, text))
        engine.never_words(["intern"])
        self.assertIn(never, engine.rewrite_problems(line, text))


class PrintOnceTest(unittest.TestCase):
    """build() filling the page: the fuller budget tried again after a sparser one fit prints the page that ran over
    before, so it isn't printed again, and the same fullest page that fits stands."""

    def test_a_page_that_ran_over_is_not_printed_again(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        printed, fits = [], engine.BUDGETS[3]  # a page up to this budget fits on one page; a fuller one runs over

        def print_pdf(html, folder, name, chrome):  # stands in for Chrome: the "PDF" is the page itself
            printed.append(html)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_text(html)
            return folder / name
        for p in (mock.patch.object(engine, "select", lambda *a, **k: {"title": "Data Engineer", "budget": a[12],
                                                                         "experience": [], "projects": []}),
                  mock.patch.object(engine, "render", lambda facts, sel: f"page {sel['budget']}"),
                  mock.patch.object(engine, "check", lambda facts, sel, pages: []),
                  mock.patch.object(engine, "print_problems", lambda page: []),
                  mock.patch.object(engine, "_print_pdf", print_pdf),
                  mock.patch.object(engine, "_pages", lambda pdf: 1 if int(Path(pdf).read_text().split()[1]) <= fits else 2),
                  mock.patch.dict(engine._FIT, {"index": 3})):
            p.start()
            self.addCleanup(p.stop)
        out = engine.build(FACTS, JD, "Data Engineer", "data-engineer", tmp, "Resume.pdf", "chrome", fill=True)
        fuller = engine.BUDGETS[2]
        self.assertEqual(printed, [f"page {fuller}", f"page {fits}"])  # one step fuller ran over, then it fit; never thrice
        self.assertEqual((out["pages"], Path(out["pdf"]).read_text(), engine._FIT["index"]), (1, f"page {fits}", 3))
        self.assertEqual(sorted(x.name for x in tmp.iterdir()), ["Resume.pdf"])


if __name__ == "__main__":
    unittest.main()
