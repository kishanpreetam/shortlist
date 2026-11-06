"""Exact mode (tailor_exact): asking for a job's resume means you have what it asks for, so a rewrite may use the
posting's skills, tools, words and ownership as yours. Numbers, limits, status, tense, fields and results stay your
line's, and no number ever comes from the posting."""
import unittest

from tailor import resume as engine
from tailor import rewrite

POSTING = ("AI Engineer. You will own agents end to end: tool calling, context engineering, RAG, evals and tracing. "
           "Build the eval and monitoring layer with regression coverage on every deploy. Strong Python and TypeScript. "
           "Experience with MCP and sub-agents. 3+ years building production software. We raised $60M. "
           "Our customers are healthcare and finance companies.")
WORDS = engine.posting_words(POSTING)
LINE = "Led and built most of a distributed system of 10 AI agents on the Anthropic Claude API, cutting disaster response from 20+ minutes to about 25 seconds in a simulated flood."


class PostingWords(unittest.TestCase):
    def test_numbers_come_out(self):
        self.assertNotRegex(WORDS, r"\d")
        self.assertIn("TypeScript", WORDS)
        self.assertIn("own agents end to end", WORDS)


class ExactRewrites(unittest.TestCase):
    def problems(self, text, posting=WORDS):
        return engine.rewrite_problems(LINE, text, frozenset(), (), (), posting)

    def test_the_postings_tools_and_practices_count_as_yours(self):
        text = ("Led and built most of a distributed system of 10 AI agents on the Anthropic Claude API with tool calling, "
                "RAG and evals, cutting disaster response from 20+ minutes to about 25 seconds in a simulated flood.")
        self.assertEqual(self.problems(text), [])
        self.assertTrue(self.problems(text, posting=""))  # outside exact mode the same rewrite fails

    def test_no_number_from_the_posting(self):
        text = LINE.replace("10 AI agents", "10 AI agents over 3+ years")
        self.assertTrue(any("number" in p for p in self.problems(text)))

    def test_a_simulation_stays_one(self):
        text = LINE.replace(" in a simulated flood", " in a flood")
        self.assertTrue(any("limit" in p for p in self.problems(text)))

    def test_no_field_from_the_posting(self):
        text = LINE.replace("disaster response", "healthcare disaster response")
        self.assertTrue(any("field" in p for p in self.problems(text)))
        robots = LINE.replace("10 AI agents", "10 AI agents testing robot policies")  # robotics is a field too
        self.assertTrue(any("robotics" in p for p in self.problems(robots)))

    def test_your_numbers_stay_attached(self):
        text = LINE.replace("20+ minutes to about 25 seconds", "25 minutes to about 20+ seconds")
        self.assertTrue(self.problems(text))


class ExactPoints(unittest.TestCase):
    FACTS = {"roles": [], "projects": [{"id": "p1", "title": "Crisis Response", "subtitle": "Lead Engineer", "status": "approved",
                                         "bullets": [{"id": "p1a", "text": LINE, "status": "approved"}]}]}

    def test_every_point_may_use_the_postings_tools(self):
        plain = rewrite.points_for(self.FACTS, "AI Engineer", POSTING)[0]["job_words"]
        wide = rewrite.points_for(self.FACTS, "AI Engineer", POSTING, exact=True)[0]["job_words"]
        self.assertNotIn("TypeScript", plain)
        self.assertIn("TypeScript", wide)

    def test_exact_answers_are_cached_apart(self):
        pts = rewrite.points_for(self.FACTS, "AI Engineer", POSTING)
        self.assertNotEqual(rewrite._cache_key(pts, "AI Engineer", POSTING),
                            rewrite._cache_key(pts, "AI Engineer", POSTING + rewrite.EXACT))



class Round4(unittest.TestCase):
    """The four refinements: posting keywords onto skills rows (exact mode), a heading shows what it names, an
    added phrase in one line only, and a phrase a rewrite adds can come back out cleanly."""

    def test_a_phrase_comes_out_of_a_list(self):
        import re
        rx = re.compile(r"(?<![\w-])tool calling(?![\w-])", re.I)
        self.assertEqual(engine.strip_tools("Built an agent harness in Python, using tool calling and evals.", [rx]),
                         "Built an agent harness in Python, using evals.")
        self.assertEqual(engine.strip_tools("Built the backend with FastAPI and tool calling.", [rx]),
                         "Built the backend with FastAPI.")

    def test_keyword_rows(self):
        self.assertEqual([engine._keyword_kind(k) for k in ("context engineering", "anomaly detection", "monitoring", "Workday")],
                         ["ai", "ml", "cloud", "data"])

    def test_exact_keywords_count_as_yours(self):
        facts = {"skills": [], "roles": [], "projects": [], "education": [], "added_skills": [], "summaries": []}
        sel = {"experience": [], "projects": [], "skills": [], "summary": None}
        kws = [{"text": "context engineering", "kind": "skill", "terms": [], "from": "read", "count": 1}]
        self.assertEqual(engine.keyword_report(kws, sel, facts)[0]["status"], "only you")
        self.assertEqual(engine.keyword_report(kws, dict(sel, exact_posting="context engineering"), facts)[0]["status"], "missing")

    def test_a_heading_word_isnt_too_general(self):
        self.assertIn(engine._root("founder"), engine._ask_roots("Solo Founder") - engine._HEADING_SKIP)
        self.assertFalse(engine._ask_roots("Data Engineer, AI Product") - engine._HEADING_SKIP)


class Proven(unittest.TestCase):
    """Proven mode (skills_proven): the skills rows list only what the page's lines show, and each role's tools
    line names the tools its own lines use."""

    def test_skills_and_tags_follow_the_lines(self):
        from tests.test_resume import FACTS, JD
        sel = engine.select(FACTS, JD, "Data Engineer", "data-engineer", proven=True)
        lines = " ".join(b["text"] for g in sel["experience"] + sel["projects"] for b in g["bullets"])
        items = [it for row in sel["skills"] if row["id"] != "learning" for it in row["items"]]
        said = {t for t in engine.terms(lines) if not t.startswith("theme:")}
        self.assertTrue(items)
        self.assertTrue(all(engine._item_terms([it]) & said or engine.keyword_shown(it, [lines]) for it in items), items)
        for g in sel["experience"]:
            own = {t for b in g["bullets"] for t in engine.terms(b["text"]) if not t.startswith("theme:")}
            self.assertTrue(all(engine._item_terms([it]) <= own for it in g["tech"]), (g["id"], g["tech"]))
        self.assertTrue(sel["proven"])
        self.assertFalse(engine.select(FACTS, JD, "Data Engineer", "data-engineer")["proven"])

    def test_a_skill_you_added_counts_when_the_job_asks(self):
        from tests.test_resume import FACTS, JD
        mine = {"id": "as_pytorch", "term": "pytorch", "label": "PyTorch", "level": "used", "status": "approved", "source": "added by you"}
        assumed = {"id": "as_jax", "term": "jax", "label": "JAX", "level": "used", "status": "approved", "assumed": True,
                   "source": "a skill this job asks for"}
        facts = dict(FACTS, added_skills=list(FACTS.get("added_skills") or []) + [mine, assumed])
        sel = engine.select(facts, JD + " Experience with PyTorch or JAX.", "Data Engineer", "data-engineer", proven=True)
        items = [it for row in sel["skills"] for it in row["items"]]
        self.assertIn("PyTorch", items)
        self.assertNotIn("JAX", items)  # a skill only this job names still needs a line


class PointEngine(unittest.TestCase):
    """New points written from the posting for your own roles: numbers only from the role's lines, the role's field
    and tense, no repeats, and the resume's punctuation rules; the passing ones join that role for one resume."""
    PLACE = {"points": ["Built Python and SQL ETL pipelines on AWS to transform 1M+ customer-interaction records into curated datasets."],
             "tense": "past"}
    GOOD = "Designed typed tool interfaces and evaluation checks for ML data pipelines in Python, tracing each failure to its root cause before release."

    def test_a_clean_point_passes(self):
        self.assertEqual(engine.generated_problems(self.GOOD, self.PLACE), [])

    def test_what_it_refuses(self):
        cases = {
            "a number of its own": self.GOOD.replace("ML data pipelines", "40 ML data pipelines"),
            "a new field": self.GOOD.replace("ML data pipelines", "patient healthcare data pipelines"),
            "the wrong tense": self.GOOD.replace("Designed", "Designing"),
            "a repeat": "Built Python and SQL ETL pipelines on AWS to transform customer-interaction records into curated datasets for models.",
            "a dash": self.GOOD.replace(", tracing", " \u2014 tracing"),
            "filler": self.GOOD.replace("Designed", "Spearheaded"),
        }
        for name, text in cases.items():
            with self.subTest(name):
                self.assertTrue(engine.generated_problems(text, self.PLACE), text)

    def test_work_not_traits_or_absolutes(self):
        cases = {"a trait": (self.GOOD.replace("tracing each failure to its root cause before release",
                                               "showing ownership beyond simple API calls"), "trait"),
                 "an absolute": (self.GOOD.replace("tracing each failure to its root cause before release",
                                                   "so failures never reached a release"), "absolute")}
        for name, (text, why) in cases.items():
            with self.subTest(name):
                self.assertTrue(any(why in w for w in engine.generated_problems(text, self.PLACE)), text)

    def test_verb_form_and_opening(self):
        current = {"points": ["Integrating OpenAI API-powered LLM features into internal applications with Python and JavaScript."],
                   "tense": "present"}
        point = "guardrails around LLM features in internal tools with structured outputs, input validation and manual review fallbacks for staff."
        self.assertTrue(any("-ing" in w for w in engine.generated_problems("Add " + point, current)))
        self.assertEqual(engine.generated_problems("Adding " + point, current), [])
        built = self.GOOD.replace("Designed", "Built")  # the place's line opens with Built
        self.assertTrue(any("opens with the verb" in w for w in engine.generated_problems(built, self.PLACE)))

    def test_a_repeated_phrase_is_caught(self):
        place = {"points": ["Built a Python and Polars data processing pipeline that reads 33,000+ live job postings from public APIs."]}
        text = "Engineered a scalable Python data processing pipeline in Polars that supports rapid experimentation on ranking logic."
        self.assertTrue(any("repeats a phrase" in w for w in engine.generated_problems(text, place)))

    def test_a_reworded_repeat_is_caught(self):
        place = {"points": ["Checked stock counts against inventory records, reviewed reported quantities alongside goods on hand, and highlighted differences requiring attention during stock reviews."], "tense": "past"}
        text = "Compared stock counts with inventory records across the full population of goods, documenting differences and escalating exceptions to owners so errors were corrected in the records."
        self.assertTrue(any("repeats" in w for w in engine.generated_problems(text, place)))

    def test_passing_points_join_their_role_for_one_resume(self):
        from tests.test_resume import FACTS
        from tailor import app
        points = [{"place": "r1", "text": "Designed evaluation checks and tracing for Airflow ETL pipelines in Python, so each failed run pointed to its cause.", "shows": ["R1"]},
                  {"place": "r1", "text": "Built ETL pipelines in Python and SQL with Airflow for 1M+ rows.", "shows": []},  # a repeat
                  {"place": "nowhere", "text": "Did something else entirely in a place that isn't yours at all, which never counts.", "shows": []}]
        got, shows = app.with_generated(FACTS, points)
        new = [b for b in next(g for g in got["roles"] if g["id"] == "r1")["bullets"] if b.get("generated")]
        self.assertEqual([b["id"] for b in new], ["r1_gen1"])
        self.assertEqual(shows, {"r1_gen1": ["R1"]})
        self.assertFalse(any(b.get("generated") for g in FACTS["roles"] for b in g["bullets"]))  # your facts stay as they are


class Wording(unittest.TestCase):
    """A rewrite keeps a whole sentence: no connecting word dropped, and the posting's words worked in, not tacked on."""
    LINES = {"js": "Integrating OpenAI API-powered LLM features into internal applications with Python and JavaScript.",
             "am": "Built Python and SQL ETL pipelines on AWS to transform 1M+ customer-interaction records.",
             "s3": "Automated ingestion using S3 and Glue, implementing validation checks to improve training-data reliability.",
             "an": "Engineered a full-stack recommendation platform with Django, React.js, and MySQL.",
             "nsf": "Analyzed data from 300+ institutions for a $10M NSF-funded alliance, building ETL pipelines, forecasting models and dashboards.",
             "ra": "Deployed real-time analytics dashboards using FastAPI and Tableau on AWS EC2 for 5,000+ users. "
                   "Orchestrated workflows with Airflow to reduce manual reporting effort by 35%."}

    def test_broken_or_tacked_on(self):
        cases = {"a dropped with": ("an", self.LINES["an"].replace("platform with", "platform"), 'drops "with"'),
                 "an aside at the end": ("js", self.LINES["js"][:-1] + ", with schema enforcement.", "tacks"),
                 "an aside between commas": ("am", self.LINES["am"].replace("AWS to", "AWS, handling state and failure modes, to"), "tacks"),
                 "a new name": ("s3", self.LINES["s3"].replace("checks to", "checks as verification loops to"), "tacks"),
                 "an aside before a period": ("an", self.LINES["an"][:-1] + ", with Python REST APIs on the backend. Deployed it on AWS.", "tacks"),
                 "a noun for its own verb": ("ra", self.LINES["ra"].replace("Airflow to reduce", "Airflow for process orchestration, reducing"),
                                             "repeats itself"),
                 "two for phrases": ("nsf", "Analyzed data from 300+ institutions for a $10M NSF-funded alliance for forecasting models and building ETL pipelines and dashboards.", "chains"),
                 "a word twice": ("am", self.LINES["am"].replace("customer-interaction records", "customer-interaction records for model training of records"),
                                  "second time")}
        for name, (key, text, why) in cases.items():
            with self.subTest(name):
                self.assertTrue(any(why in w for w in engine.wording_problems(self.LINES[key], text)), text)

    def test_no_posting_phrase_for_your_word(self):
        line = "Checked stock counts against inventory records and highlighted differences requiring attention."
        swapped = line.replace("differences", "state drift")
        self.assertTrue(any("swaps" in w for w in engine.wording_problems(line, swapped, ["state drift", "Python"])))
        self.assertEqual(engine.wording_problems(line, swapped), [])  # only the posting's phrases count as a swap

    def test_worked_in_words_pass(self):
        self.assertEqual(engine.wording_problems(self.LINES["js"], self.LINES["js"].replace("features into", "features and AI agents into")), [])
        self.assertEqual(engine.wording_problems(self.LINES["ra"], "Deployed real-time analytics dashboards on AWS EC2 for 5,000+ users, "
                                                 "serving data through FastAPI REST APIs and Tableau, and orchestrated workflows with Airflow "
                                                 "to reduce manual reporting effort by 35%."), [])

    def test_guardrails_and_evals_are_read(self):
        self.assertIn("guardrails", engine.terms("Implement programmatic guardrails, schema enforcement and human-in-the-loop fallback triggers"))
        self.assertIn("guardrails", engine.terms("Added a human approval checkpoint before high-stakes actions could be executed."))
        self.assertIn("evals", engine.terms("Build evaluation suites and automated verification loops for agents"))


class JobChoices(unittest.TestCase):
    """Lines pinned on or taken off one job's page are kept for every later build of that job."""

    def test_saved_toggled_and_reset(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from tailor import app
        tmp = Path(tempfile.mkdtemp()) / "job-choices.json"
        with mock.patch.object(app, "CHOICES", tmp):
            self.assertEqual(app.job_choices("u1", ["am_feat"], ["js_dash"]), {"include": ["am_feat"], "exclude": ["js_dash"]})
            self.assertEqual(app.job_choices("u1"), {"include": ["am_feat"], "exclude": ["js_dash"]})  # kept
            self.assertEqual(app.job_choices("u1", ["js_dash"]), {"include": ["am_feat", "js_dash"], "exclude": []})
            self.assertEqual(app.job_choices("u2"), {"include": [], "exclude": []})  # per job
            self.assertEqual(app.job_choices("u1", reset=True), {"include": [], "exclude": []})


if __name__ == "__main__":
    unittest.main()
