import copy
import unittest

from shortlist import resume, rewrite

FACTS = {
    "contact": {"name": "Sam Example", "lines": ["Boston, MA", "sam@example.com"], "links": [], "status": "approved"},
    "titles": {"items": ["Data Engineer", "Data Analyst", "Software Engineer"], "status": "approved"},
    "summaries": [{"id": "s1", "variant": "data-engineer", "text": "Data engineer who builds ETL pipelines in Python and SQL.", "status": "approved"}],
    "education": [{"school": "State University", "degree": "M.S. Data Science", "date": "2025", "status": "approved"}],
    "skills": [{"id": "sk_data", "label": "Data", "items": ["ETL", "Airflow", "SQL"], "status": "approved"},
               {"id": "sk_ml", "label": "ML", "items": ["Forecasting", "Tableau"], "status": "approved"}],
    "roles": [
        {"id": "r1", "title": "Data Engineer", "org": "Acme", "place": "", "dates": "2023 – 2024", "status": "approved", "bullets": [
            {"id": "r1a", "text": "Built ETL pipelines in Python and SQL with Airflow for 1M+ rows.", "status": "approved"},
            {"id": "r1b", "text": "Built Tableau dashboards for 500 users.", "status": "approved"},
            {"id": "r1c", "text": "Wrote a Kafka streaming job.", "status": "pending"},
        ]},
        {"id": "r2", "title": "Research Assistant", "org": "State University", "place": "", "dates": "2022", "status": "approved", "bullets": [
            {"id": "r2a", "text": "Raised forecast accuracy from 70% to 90%.", "status": "approved"},
        ]},
    ],
    "projects": [],
    "variants": {"data-engineer": ["r1a"]},
}
JD = "We need a data engineer with SQL, Airflow and Kafka to build ETL pipelines and dashboards."


class SelectTest(unittest.TestCase):
    def test_only_approved_lines_and_exact_text(self):
        sel = resume.select(FACTS, JD, "Senior Data Engineer, Platform", "data-engineer")
        ids = [b["id"] for g in sel["experience"] for b in g["bullets"]]
        self.assertNotIn("r1c", ids)  # pending: never used, even though the job asks for Kafka
        self.assertEqual(resume.check(FACTS, sel, 1), [])
        self.assertIn("Kafka", sel["missing"])  # reported, never added

    def test_title_is_an_approved_title_not_their_wording(self):
        sel = resume.select(FACTS, JD, "Senior Data Engineer, Platform", "data-engineer")
        self.assertTrue(sel["title_line"].startswith("Data Engineer"))
        self.assertNotIn("Senior", sel["title_line"])
        for skill in sel["title_line"].split(" · ")[1:]:
            self.assertIn(skill, sel["covered"])

    def test_every_role_keeps_a_bullet_in_date_order(self):
        sel = resume.select(FACTS, JD, "Data Engineer", "data-engineer")
        self.assertEqual([g["id"] for g in sel["experience"]], ["r1", "r2"])

    def test_a_line_you_add_from_any_project_goes_on(self):
        facts = copy.deepcopy(FACTS)
        facts["projects"] = [{"id": f"p{i}", "title": f"Project {i}", "subtitle": "", "status": "approved", "bullets": [
            {"id": f"p{i}a", "text": f"Built project {i} with {tool} for {n} users.", "status": "approved"}]}
            for i, (tool, n) in enumerate((("SQL and Airflow ETL pipelines", 100), ("Tableau dashboards", 200), ("a website", 300)))]
        sel = resume.select(facts, JD, "Data Engineer", "data-engineer")
        self.assertNotIn("p2a", bullet_texts(sel))  # the least relevant of 3 projects is left off by default
        sel = resume.select(facts, JD, "Data Engineer", "data-engineer", include={"p2a"})
        self.assertIn("p2a", bullet_texts(sel))  # tapping + puts it on
        self.assertEqual(len(sel["projects"]), resume.MAX_PROJECTS)

    def test_exclude_removes_a_bullet(self):
        sel = resume.select(FACTS, JD, "Data Engineer", "data-engineer", exclude={"r1b"})
        self.assertNotIn("r1b", [b["id"] for g in sel["experience"] for b in g["bullets"]])

    def test_check_catches_changed_text_and_em_dashes(self):
        sel = resume.select(FACTS, JD, "Data Engineer", "data-engineer")
        bad = copy.deepcopy(sel)
        bad["experience"][0]["bullets"][0]["text"] += " Also led a team of 10."
        self.assertTrue(any("exactly" in f for f in resume.check(FACTS, bad, 1)))
        facts = copy.deepcopy(FACTS)
        facts["roles"][1]["bullets"][0]["text"] = "Raised accuracy — a lot."
        sel2 = resume.select(facts, JD, "Data Engineer", "data-engineer")
        self.assertTrue(any("em dash" in f for f in resume.check(facts, sel2, 1)))

    def test_nothing_without_approved_contact(self):
        facts = copy.deepcopy(FACTS)
        facts["contact"]["status"] = "pending"
        sel = resume.select(facts, JD, "Data Engineer", "data-engineer")
        self.assertTrue(resume.check(facts, sel, 1))

    def test_two_pages_fails(self):
        sel = resume.select(FACTS, JD, "Data Engineer", "data-engineer")
        self.assertTrue(any("2 pages" in f for f in resume.check(FACTS, sel, 2)))

    def test_title_rules(self):
        allowed = ["Data Engineer", "Data Analyst", "Software Engineer", "AI Engineer"]
        self.assertEqual(resume.choose_title("Business Intelligence Analyst", allowed, "data-analyst"), "Data Analyst")
        self.assertEqual(resume.choose_title("Software Engineer II, Backend", allowed, "ai-engineer"), "Software Engineer")
        self.assertEqual(resume.choose_title("Business Consultant", allowed, "data-analyst"), "Data Analyst")


def with_versions(status="approved", text="Built Airflow ETL pipelines in Python and SQL for 1M+ rows, with data quality checks.", adds=("theme:data_quality",)):
    facts = copy.deepcopy(FACTS)
    facts["roles"][0]["bullets"][0]["alts"] = [{"id": "r1a~de", "lens": "data-engineer", "text": text, "adds": list(adds), "status": status}]
    return facts


def bullet_texts(sel):
    return {b["id"]: b["text"] for g in sel["experience"] + sel["projects"] for b in g["bullets"]}


class VersionTest(unittest.TestCase):
    JD_DQ = JD + " You care about data quality."

    def test_approved_version_used_when_it_fits_better(self):
        facts = with_versions()
        sel = resume.select(facts, self.JD_DQ, "Data Engineer", "data-engineer")
        self.assertEqual(bullet_texts(sel)["r1a"], facts["roles"][0]["bullets"][0]["alts"][0]["text"])
        self.assertEqual(resume.check(facts, sel, 1), [])
        mine = resume.select(facts, self.JD_DQ, "Data Engineer", "data-engineer", original={"r1a"})
        self.assertEqual(bullet_texts(mine)["r1a"], FACTS["roles"][0]["bullets"][0]["text"])

    def test_pending_version_never_used_only_offered(self):
        facts = with_versions(status="pending")
        sel = resume.select(facts, self.JD_DQ, "Data Engineer", "data-engineer")
        r1a = next(b for g in sel["experience"] for b in g["bullets"] if b["id"] == "r1a")
        self.assertEqual(r1a["text"], FACTS["roles"][0]["bullets"][0]["text"])
        self.assertEqual(r1a["waiting"]["id"], "r1a~de")
        bad = copy.deepcopy(sel)  # a pending version slipped onto the page must fail the check
        bad["experience"][0]["bullets"][0]["text"] = facts["roles"][0]["bullets"][0]["alts"][0]["text"]
        self.assertTrue(any("exactly" in f for f in resume.check(facts, bad, 1)))

    def test_a_version_that_changes_a_fact_is_never_used(self):
        for text in ("Built ETL pipelines in Python and SQL with Airflow for 5M+ rows.",        # a number changed
                     "Built ETL pipelines in Python, SQL and Kafka with Airflow for 1M+ rows.",  # a tool it doesn't declare
                     "Led ETL pipelines in Python and SQL with Airflow for 1M+ rows."):          # more scope
            facts = with_versions(text=text, adds=())
            self.assertTrue(resume.faithful(FACTS["roles"][0]["bullets"][0]["text"], facts["roles"][0]["bullets"][0]["alts"][0]))
            sel = resume.select(facts, JD + " Kafka leadership", "Data Engineer", "data-engineer")
            self.assertEqual(bullet_texts(sel)["r1a"], FACTS["roles"][0]["bullets"][0]["text"])

    def test_same_terms_tie_goes_to_your_wording_unless_written_for_this_job_type(self):
        text = "Processed 1M+ rows with ETL pipelines in Python and SQL, orchestrated with Airflow."
        facts = with_versions(text=text, adds=())
        self.assertEqual(bullet_texts(resume.select(facts, JD, "Data Engineer", "data-engineer"))["r1a"], text)
        self.assertEqual(bullet_texts(resume.select(facts, JD, "Data Analyst", "data-analyst"))["r1a"], FACTS["roles"][0]["bullets"][0]["text"])


class RewriteTest(unittest.TestCase):
    """Claude's rewrites of your points for one job (rewrite.py) are used only where they keep the line's facts."""
    MINE = FACTS["roles"][0]["bullets"][0]["text"]
    GOOD = "Engineered Airflow ETL pipelines in Python and SQL to process 1M+ rows."  # no tool the line doesn't name

    @staticmethod
    def facts():
        facts = copy.deepcopy(FACTS)
        facts["roles"][0]["tools"] = ["Python", "SQL", "Airflow", "Spark"]
        return facts

    def test_a_tool_from_the_roles_tools_line_is_allowed_and_no_other(self):
        text = "Engineered Airflow ETL pipelines in Python and Spark SQL to process 1M+ rows."  # Spark: on the role's tools line
        self.assertEqual(resume.faithful_rewrites(self.facts(), {"r1a": text}), {"r1a": text})
        other = "Engineered Airflow ETL pipelines in Python and Snowflake SQL to process 1M+ rows."  # Snowflake: not the role's
        self.assertEqual(resume.faithful_rewrites(self.facts(), {"r1a": other}), {})
        self.assertTrue(any("Snowflake" in p for p in resume.rewrite_problems(self.MINE, other, resume.mapped_tools(self.facts(), "r1"))))
        self.assertEqual(resume.rewrite_terms(self.MINE), set(resume.terms(self.MINE)))
        self.assertIn("etl", resume.rewrite_terms("Orchestrated workflows with Airflow."))  # a pipeline line may say ETL

    def test_several_confirmed_tools_fit_and_their_names_dont_count_against_the_length(self):
        facts = self.facts()  # r1's tools line: Python, SQL, Airflow, Spark
        facts["added_skills"] = [{"term": t, "level": "used", "status": "approved", "where": ["r1"]} for t in ("snowflake", "dbt", "kafka")]
        text = "Built ETL pipelines in Python and SQL with Airflow, Spark, Snowflake, dbt and Kafka for 1M+ rows."
        self.assertEqual(resume.faithful_rewrites(facts, {"r1a": text}), {"r1a": text})
        padded = "Built ETL pipelines in Python and SQL with Airflow for 1M+ rows, every single day, again and again, as the data grew larger."
        self.assertEqual(resume.faithful_rewrites(facts, {"r1a": padded}), {})  # extra words still count
        self.assertNotIn("at most two", rewrite.RULES)

    def test_a_tools_name_is_never_a_claim(self):
        mine = "Built chatbot pages for the consortium website."
        tools = {"openai", "javascript", "python"}  # the role's own tools line
        self.assertEqual(resume.rewrite_problems(mine, "Built chatbot pages in Python and JavaScript on the OpenAI API for the consortium website.", tools), [])
        self.assertTrue(resume.rewrite_problems(mine, "Built and deployed chatbot pages on the OpenAI API for the consortium website.", tools))
        api = "Built semantic search on the OpenAI API for 500 users."  # "API" doesn't make the line a production line
        self.assertTrue(resume.rewrite_problems(api, "Built and deployed semantic search on the OpenAI API for 500 users."))
        self.assertNotIn("theme:production", resume.rewrite_terms(api))

    def test_a_rewrite_that_keeps_the_facts_is_used(self):
        facts = self.facts()
        sel = resume.select(facts, JD, "Data Engineer", "data-engineer", rewrites={"r1a": self.GOOD})
        r1a = next(b for g in sel["experience"] for b in g["bullets"] if b["id"] == "r1a")
        self.assertEqual((r1a["text"], r1a["version"]), (self.GOOD, "job"))
        self.assertEqual(sel["rewrites"], {"r1a": self.GOOD})
        self.assertEqual(resume.check(facts, sel, 1), [])
        mine = resume.select(facts, JD, "Data Engineer", "data-engineer", rewrites={"r1a": self.GOOD}, original={"r1a"})
        self.assertEqual(bullet_texts(mine)["r1a"], self.MINE)  # "my wording"

    def test_the_jobs_words_for_the_same_work_are_allowed_new_work_is_not(self):
        facts = self.facts()
        same = "Built Tableau reporting for 500 users."  # reporting: the job's word for your dashboards
        self.assertEqual(resume.faithful_rewrites(facts, {"r1b": same}), {"r1b": same})
        for text in ("Automated ETL pipelines in Python and SQL with Airflow for 1M+ rows.",  # automation: a new claim
                     "Built production ETL pipelines in Python and SQL with Airflow for 1M+ rows."):
            self.assertEqual(resume.faithful_rewrites(facts, {"r1a": text}), {})

    def test_verb_forms_and_data_pipelines_are_not_new_claims(self):
        facts = self.facts()
        facts["roles"][0]["bullets"][0]["text"] = "Architected pipelines in Python for 1M+ rows."
        text = "Processed 1M+ rows by architecting Python data pipelines."
        self.assertEqual(resume.faithful_rewrites(facts, {"r1a": text}), {"r1a": text})
        self.assertEqual(resume.faithful_rewrites(facts, {"r1a": "Led Python data pipelines for 1M+ rows."}), {})

    def test_a_rewrite_that_changes_a_fact_is_dropped(self):
        bad = {"a number": "Built ETL pipelines in Python and SQL with Airflow for 5M+ rows.",
               "a tool the role doesn't list": "Built ETL pipelines in Python, SQL and Kafka with Airflow for 1M+ rows.",
               "more scope": "Led ETL pipelines in Python and SQL with Airflow for 1M+ rows.",
               "a new claim": "Built ETL pipelines in Python and SQL with Airflow for 1M+ rows with LLM agents.",
               "a dash": "Built ETL pipelines in Python and SQL with Airflow \u2014 1M+ rows.",
               "padding": self.MINE[:-1] + ", one table at a time, each and every single night of the week."}
        facts = self.facts()
        for why, text in bad.items():
            with self.subTest(why):
                self.assertEqual(resume.faithful_rewrites(facts, {"r1a": text}), {})
                sel = resume.select(facts, JD + " Kafka, LLM agents, leadership", "Data Engineer", "data-engineer", rewrites={"r1a": text})
                self.assertEqual(bullet_texts(sel)["r1a"], self.MINE)
                forged = copy.deepcopy(sel)  # slipped onto the page anyway: the check fails it
                forged["experience"][0]["bullets"][0]["text"], forged["rewrites"] = text, {"r1a": text}
                self.assertTrue(any("exactly" in f for f in resume.check(facts, forged, 1)))

    def test_pending_lines_and_unknown_ids_are_never_rewritten_in(self):
        rewrites = {"r1c": "Wrote a Kafka streaming job in Python.", "zzz": "Built a rocket."}
        self.assertEqual(resume.faithful_rewrites(self.facts(), rewrites), {})
        sel = resume.select(self.facts(), JD, "Data Engineer", "data-engineer", rewrites=rewrites)
        self.assertNotIn("r1c", bullet_texts(sel))

    def kept(self, line: str, text: str) -> bool:
        """Whether a rewrite of `line` (put in as bullet r1a) survives the fact check."""
        facts = self.facts()
        facts["roles"][0]["bullets"][0]["text"] = line
        return resume.faithful_rewrites(facts, {"r1a": text}) == {"r1a": text}

    def test_a_word_the_line_doesnt_have_is_dropped(self):
        bad = {"planted in the posting": "Built ETL pipelines in Python and SQL with Airflow for 1M+ zephyr rows.",
               "an unknown tool": "Built ETL pipelines in Python and SQL with Prefect and Airflow for 1M+ rows.",
               "another unknown tool": "Built ETL pipelines in Python, SQL and Dask with Airflow for 1M+ rows.",
               "a domain": "Built ETL pipelines in Python and SQL with Airflow for 1M+ patient claims rows.",
               "people": "Built ETL pipelines in Python and SQL with Airflow for 1M+ rows for stakeholders."}
        for why, text in bad.items():
            with self.subTest(why):
                self.assertFalse(self.kept(self.MINE, text))
        self.assertIn("zephyr", resume.rewrite_problems(self.MINE, bad["planted in the posting"])[0])
        self.assertTrue(self.kept(self.MINE, "Built Airflow ETL data pipelines in Python and SQL that process 1M+ rows."))
        # a word the engine reads as a theme your line has only through a weaker cue: "API" isn't production
        self.assertFalse(self.kept("Built semantic search on the OpenAI API for 500 users.",
                                   "Built production semantic search on the OpenAI API for 500 users."))
        self.assertTrue(self.kept("Deployed semantic search on AWS for 500 users.",
                                  "Deployed production semantic search on AWS for 500 users."))

    def test_numbers_keep_their_words_and_their_order(self):
        bad = [("Built Tableau dashboards for 500 users.", "Built Tableau dashboards for five teams of 500 users."),
               ("Raised forecast accuracy from 70% to 90%.", "Raised forecast accuracy from 90% to 70%."),
               ("Cut response time from 20+ minutes to about 25 seconds.", "Cut response time from about 25 seconds to 20+ minutes."),
               ("Cut response time from 20+ minutes to about 25 seconds.", "Cut response time from 20+ seconds to about 25 minutes."),
               ("Processed 1M+ rows from 300+ institutions.", "Processed 300+ rows from 1M+ institutions.")]
        for line, text in bad:
            with self.subTest(text):
                self.assertFalse(self.kept(line, text))
                self.assertTrue(resume.faithful(line, {"text": text}))  # your approved versions get the same check
        self.assertEqual(resume._pairs("from 20+ minutes to about 25 seconds"), [("20+", "25")])
        self.assertEqual(resume._pairs("replacing 20-30 minutes"), resume._pairs("replacing 20 to 30 minutes"))

    def test_reorders_and_verb_forms_are_kept(self):
        good = [("Deployed X dashboards for 5,000+ users. Orchestrated Y workflows to reduce manual effort by 35%.",
                 "Reduced manual effort by 35% through Y workflows. Deployed X dashboards for 5,000+ users."),
                ("Raised forecast accuracy from 70% to 90%.", "Improved forecast accuracy from 70% to 90%."),
                ("Replaced 20-30 minutes of manual research with Python.", "Replaced 20 to 30 minutes of manual research with Python."),
                # verb forms in your line's tense (R5: "Leading" for a finished "Led" now fails, see test_factcheck)
                ("Leading and building ETL pipelines in Python for 1M+ rows.", "Leads and builds ETL pipelines in Python for 1M+ rows."),
                ("Led and built ETL pipelines in Python for 1M+ rows.", "Built ETL pipelines in Python for 1M+ rows, leading the work."),
                ("Built ETL pipelines in Python for 1M+ rows, reviewing them.", "Reviewed ETL pipelines in Python built for 1M+ rows.")]
        for line, text in good:
            with self.subTest(text):
                self.assertTrue(self.kept(line, text), resume.rewrite_problems(line, text))
        alt = {"text": "Cut manual reporting effort by 35% with real-time dashboards (Tableau, FastAPI) for 5,000+ users, "
                       "deployed on AWS EC2 and orchestrated with Airflow."}  # an approved version that moves the numbers
        self.assertEqual(resume.faithful("Deployed real-time dashboards using FastAPI and Tableau on AWS EC2 for 5,000+ users. "
                                         "Orchestrated workflows with Airflow to reduce manual reporting effort by 35%.", alt), [])

    def test_more_scope_is_dropped(self):
        bad = [(self.MINE, "Took ownership of ETL pipelines in Python and SQL with Airflow for 1M+ rows."),
               (self.MINE, "Built end to end ETL pipelines in Python and SQL with Airflow for 1M+ rows."),
               (self.MINE, "Built end\u2011to\u2011end ETL pipelines in Python and SQL with Airflow for 1M+ rows."),
               (self.MINE, "Independently built ETL pipelines in Python and SQL with Airflow for 1M+ rows."),
               ("Built a scheduler that lets users manage their own schedules.", "Owned a scheduler that lets users manage schedules."),
               ("Built a scheduler that lets users manage their own schedules.", "Managed a scheduler that lets users own schedules."),
               ("Interviewed department heads to define reports.", "Headed interviews with departments to define reports."),
               ("Built a router that directs requests to services.", "Directed requests to services through a router."),
               ("Trained supervised learning models on 1M+ rows.", "Supervised the training of models on 1M+ rows.")]
        for line, text in bad:
            with self.subTest(text):
                self.assertFalse(self.kept(line, text))
                self.assertTrue(any("scope" in w for w in resume.faithful(line, {"text": text})))
        self.assertTrue(self.kept("Owned product development and deployment independently.",
                                  "Independently owned product development and deployment."))

    def test_arrows_long_dashes_and_added_colons_are_dropped(self):
        for text in (self.MINE.replace(" for ", " \u2192 "), self.MINE.replace(" for ", " -\u003e "), self.MINE.replace(" for ", " \u2013 "),
                     self.MINE.replace(" in ", ": "), self.MINE.replace(" with ", "; ")):
            with self.subTest(text):
                self.assertFalse(self.kept(self.MINE, text))
        line = "Built dashboards across 3 data systems: Salesforce and SQL."
        self.assertTrue(self.kept(line, "Built dashboards across 3 data systems: SQL and Salesforce."))  # your own colon stays
        for odd in ("\U0001F680", "\u200b", "\u202e"):  # an emoji, a zero-width space, a right-to-left override
            with self.subTest(hex(ord(odd))):
                self.assertFalse(self.kept(self.MINE, self.MINE.replace(" for ", f" {odd}for ")))

    def test_an_available_line_says_it_is_a_rewrite(self):
        same = "Built Tableau reporting for 500 users."
        sel = resume.select(self.facts(), JD, "Data Engineer", "data-engineer", rewrites={"r1b": same}, drop={"r1b"})
        r1b = next(b for b in sel["available"] if b["id"] == "r1b")
        self.assertEqual((r1b["text"], r1b["version"], r1b["original"]), (same, "job", FACTS["roles"][0]["bullets"][1]["text"]))
        mine = resume.select(self.facts(), JD, "Data Engineer", "data-engineer", drop={"r1b"})
        self.assertNotIn("original", next(b for b in mine["available"] if b["id"] == "r1b"))


class PrintTest(unittest.TestCase):
    """Your periods and colons print as you wrote them; the summary never has a colon or semicolon; no dashes."""

    def test_lines_keep_your_punctuation(self):
        self.assertEqual(resume.sentence("Deployed dashboards for 5,000+ users. Orchestrated workflows with Airflow."),
                         "Deployed dashboards for 5,000+ users. Orchestrated workflows with Airflow.")
        self.assertEqual(resume.sentence("Built SQL pipelines for 1M+ rows"), "Built SQL pipelines for 1M+ rows.")  # a rewrite gets its period
        self.assertEqual(resume.sentence("Across 3 systems: SQL and Salesforce."), "Across 3 systems: SQL and Salesforce.")
        self.assertEqual(resume.printed("Replaced 20\u201330 minutes of research."), "Replaced 20-30 minutes of research.")
        self.assertEqual(resume.printed_dates("May 2026 \u2013 Present"), "May 2026 - Present")  # one plain hyphen
        self.assertEqual(resume.printed_dates("May 2026 to Present"), "May 2026 - Present")
        self.assertEqual(resume.printed_dates("2025"), "2025")

    def test_the_summary_never_has_a_colon_or_semicolon(self):
        self.assertEqual(resume.printed_summary("Data engineer who builds pipelines: ETL on AWS; dashboards in Tableau"),
                         "Data engineer who builds pipelines, ETL on AWS, dashboards in Tableau.")
        page = resume.render(FACTS, resume.select(FACTS, JD, "Data Engineer", "data-engineer"))
        self.assertEqual(resume.print_problems(page), [])
        self.assertIn("2023 - 2024", page)
        self.assertIn("font-variant-ligatures:none", page)  # fi and fl stay two letters, so keywords never break

    def test_a_dash_or_a_colon_in_the_summary_stops_the_pdf(self):
        for bad in ("<p>Used Airflow -- daily</p>", "<p>Used Airflow \u2014 daily</p>", "<p>2023 \u2013 2024</p>",
                    "<h2>Summary</h2><p>Data engineer: pipelines.</p>", "<h2>Summary</h2><p>Data engineer; pipelines.</p>"):
            with self.subTest(bad):
                self.assertTrue(resume.print_problems(bad))
        self.assertEqual(resume.print_problems("<h2>Summary</h2><p>Data engineer.</p><ul><li>Across 3 systems: SQL.</li></ul>"), [])


class SkillsTest(unittest.TestCase):
    def skill_items(self, sel):
        return {s["id"]: s["items"] for s in sel["skills"]}

    def test_a_skill_your_lines_back_joins_its_line(self):
        facts = copy.deepcopy(FACTS)
        facts["roles"][0]["bullets"][1]["text"] = "Built Tableau and Looker dashboards for 500 users."
        sel = resume.select(facts, JD + " Looker", "Data Analyst", "data-analyst")
        self.assertIn("Looker", self.skill_items(sel)["sk_ml"])
        self.assertEqual(resume.check(facts, sel, 1), [])

    def test_added_skills_fill_rows_and_learning_only_when_named(self):
        facts = copy.deepcopy(FACTS)
        facts["added_skills"] = [{"term": "dbt", "level": "used", "status": "approved"},
                                 {"term": "snowflake", "level": "learning", "status": "approved"},
                                 {"term": "databricks", "level": "used", "status": "pending"}]
        sel = resume.select(facts, JD + " dbt, Snowflake and Databricks.", "Data Engineer", "data-engineer")
        items = self.skill_items(sel)
        self.assertIn("dbt", items["sk_data"])
        self.assertEqual(items["learning"], ["Snowflake"])
        self.assertNotIn("Databricks", " ".join(sum(items.values(), [])))  # not approved: never added
        self.assertNotIn("Snowflake", sel["title_line"])  # learning skills never go on the title line
        self.assertEqual(resume.check(facts, sel, 1), [])
        plain = resume.select(facts, JD, "Data Engineer", "data-engineer")
        self.assertNotIn("learning", self.skill_items(plain))  # learning skills only for jobs that name them
        self.assertNotIn("Databricks", " ".join(sum(self.skill_items(plain).values(), [])))  # pending: never

    def test_check_rejects_a_skill_nobody_approved(self):
        sel = resume.select(FACTS, JD, "Data Engineer", "data-engineer")
        bad = copy.deepcopy(sel)
        bad["skills"][0]["items"].append("Kafka")
        self.assertTrue(any("isn't approved" in f for f in resume.check(FACTS, bad, 1)))
        self.assertIn("Kafka", sel["missing"])

    def test_skill_skip_leaves_it_off(self):
        sel = resume.select(FACTS, JD, "Data Engineer", "data-engineer", skill_skip={"Airflow"})
        self.assertNotIn("Airflow", sum(self.skill_items(sel).values(), []))

    def test_the_employers_own_name_is_not_a_skill(self):
        jd = "Snowflake Inc. is hiring. At Snowflake, you will use SQL and Tableau. Snowflake's mission matters."
        self.assertNotIn("snowflake", resume.terms(resume.without_employer(jd, "Snowflake Inc")))
        self.assertIn("sql", resume.terms(resume.without_employer(jd, "Snowflake Inc")))
        self.assertNotIn("anthropic", resume.terms("Anthropic is an AI safety company. Anthropic's teams..."))
        self.assertIn("anthropic", resume.terms("Experience with the Claude API"))
        self.assertNotIn("openai", resume.terms("Our team worked on GPT-3 before joining us."))  # history, not a skill ask
        self.assertNotIn("excel", resume.terms("You'll have the tools and opportunity to excel."))
        self.assertIn("excel", resume.terms("Proficiency with Microsoft Office (Excel, Word)"))
        self.assertNotIn("anthropic", resume.terms("Use AI coding tools such as Claude Code or Cursor"))
        self.assertIn("ai_coding", resume.terms("Use AI coding tools such as Claude Code or Cursor"))
        sel = resume.select(FACTS, jd, "Data Analyst", "data-analyst", employer="Snowflake Inc")
        self.assertNotIn("Snowflake", sel["missing"])

    def test_skills_rows_are_one_line_and_only_approved_items(self):
        facts = copy.deepcopy(FACTS)
        facts["skills"][0]["items"] = ["ETL", "Airflow", "SQL"] + [f"Tool{i}" for i in range(40)]
        sel = resume.select(facts, JD, "Data Engineer", "data-engineer")
        for s in sel["skills"]:
            self.assertLessEqual(len(", ".join(s["items"])), resume.SKILL_CHARS)
        self.assertEqual(resume.check(facts, sel, 1), [])

    def test_every_skill_label_reads_back_as_its_skill(self):
        for t in resume.vocabulary():
            self.assertIn(t, resume._item_terms([resume.item_label(t)]), t)
            self.assertIn(t, resume.CATEGORY, t)


class YearsTest(unittest.TestCase):
    def test_years_count_each_month_once(self):
        from datetime import date
        facts = copy.deepcopy(FACTS)
        facts["roles"][0]["dates"] = "Apr 2021 – Jun 2023"
        facts["roles"][1]["dates"] = "Nov 2022 – Apr 2023"  # overlaps: no extra months
        self.assertEqual(resume.experience_years(facts, date(2026, 10, 14)), 2)  # 27 months
        facts["roles"][1]["dates"] = "Jul 2023 – Present"
        self.assertEqual(resume.experience_years(facts, date(2026, 10, 14)), 5)  # 27 + 40 months

    def test_summary_says_your_years(self):
        facts = copy.deepcopy(FACTS)
        facts["summaries"][0].pop("text")
        facts["summaries"][0].update(opener="{title} with {years}+ years building pipelines.", closer="M.S., State University.")
        facts["impacts"] = [{"id": "i1", "backs": "r1a", "lens": ["data-engineer"], "status": "approved",
                             "text": "Built Airflow ETL pipelines in Python and SQL for 1M+ rows."}]
        sel = resume.select(facts, JD, "Data Engineer", "data-engineer")
        self.assertTrue(sel["summary"]["text"].startswith("Data Engineer with 3+ years"))  # 2022 through 2024
        self.assertEqual(resume.check(facts, sel, 1), [])


class JobToolsTest(unittest.TestCase):
    """Every tool a job asks for that you have reaches the page: in the summary, in the tools line of a role you
    said you used it in, and in that role's rewritten lines. Never at a role you didn't name."""
    JD_SF = JD + " Snowflake and Azure Data Factory are strongly preferred, plus PySpark."

    @staticmethod
    def facts(where=("r1",)):
        facts = copy.deepcopy(FACTS)
        facts["summaries"][0].pop("text")
        facts["summaries"][0].update(opener="{title} with {years}+ years building pipelines.", closer="M.S., State University.")
        facts["added_skills"] = [{"id": "as_sf", "term": "snowflake", "label": "Snowflake", "level": "used", "status": "approved",
                                  "where": list(where)},
                                 {"id": "as_adf", "term": "azure_data_factory", "label": "Azure Data Factory", "level": "used",
                                  "status": "approved", "where": ["r1"]}]
        return facts

    def test_the_engine_sees_cloud_data_tools(self):
        for phrase, term in (("Azure Data Factory", "azure_data_factory"), ("ADLS Gen2", "adls"), ("Microsoft Fabric", "fabric"),
                             ("PySpark notebooks", "pyspark"), ("Azure Event Hubs", "event_hubs"), ("relational databases", "relational_db")):
            self.assertIn(term, resume.terms(phrase), phrase)

    def test_the_summary_names_the_jobs_tools_you_have(self):
        facts = self.facts()
        sel = resume.select(facts, self.JD_SF, "Data Engineer", "data-engineer")
        self.assertIn("Builds data pipelines and platforms with ", sel["summary"]["text"])  # confident, never "hands-on"
        self.assertNotIn("Hands-on", sel["summary"]["text"])
        self.assertEqual(set(sel["summary"]["tools"]), {"Snowflake", "Azure Data Factory"})  # not PySpark: you haven't added it
        facts["added_skills"].append({"id": "as_ps", "term": "pyspark", "label": "PySpark", "level": "used", "status": "approved"})
        self.assertNotIn("PySpark", resume.summary_tools(facts, resume.terms(self.JD_SF)))  # added, but tied to no role or project
        self.assertEqual(resume.check(facts, sel, 1), [])
        forged = copy.deepcopy(sel)
        forged["summary"]["text"] = forged["summary"]["text"].replace("Snowflake", "Snowflake and PySpark")
        forged["summary"]["tools"] = forged["summary"]["tools"] + ["PySpark"]
        self.assertTrue(resume.check(facts, forged, 1))

    def test_the_specific_tool_wins_and_ideas_stay_on_skills_lines(self):
        facts = self.facts()
        facts["added_skills"] += [{"id": "as_az", "term": "azure", "label": "Azure", "level": "used", "status": "approved"},
                                  {"id": "as_dl", "term": "data_lake", "label": "Data Lakes", "level": "used", "status": "approved"}]
        tools = resume.summary_tools(facts, resume.terms(self.JD_SF + " Azure data lake."))
        self.assertIn("Azure Data Factory", tools)
        self.assertNotIn("Azure", tools)  # said by Azure Data Factory
        self.assertNotIn("Data Lakes", tools)  # an idea, not a tool to build with

    def test_a_rewrite_never_drops_a_strong_word(self):
        mine = "Architected production ETL pipelines in Python for 1M+ rows."
        self.assertTrue(resume.rewrite_problems(mine, "Built ETL pipelines in Python for 1M+ rows."))
        self.assertEqual(resume.rewrite_problems(mine, "Architected production Python ETL pipelines for 1M+ rows."), [])

    def test_a_tool_reaches_the_lines_and_tools_line_only_where_you_used_it(self):
        facts = self.facts(where=("r1",))
        text = "Built ETL pipelines in Python and SQL with Airflow for 1M+ rows into Snowflake."
        self.assertEqual(resume.faithful_rewrites(facts, {"r1a": text}), {"r1a": text})  # r1: you said you used it there
        other = "Raised forecast accuracy from 70% to 90% in Snowflake."
        self.assertEqual(resume.faithful_rewrites(facts, {"r2a": other}), {})  # r2: you didn't
        sel = resume.select(facts, self.JD_SF, "Data Engineer", "data-engineer", rewrites={"r1a": text})
        r1 = next(g for g in sel["experience"] if g["id"] == "r1")
        self.assertIn("Snowflake", " ".join(b["text"] for b in r1["bullets"]))  # in its line, so not again on its tools line
        self.assertNotIn("Snowflake", r1["tech"])
        self.assertNotIn("Snowflake", next(g for g in sel["experience"] if g["id"] == "r2")["tech"])
        self.assertEqual(resume.check(facts, sel, 1), [])


class NumberTest(unittest.TestCase):
    """Lines that have a number are preferred on a tailored resume."""

    def test_a_line_with_a_number_wins_a_tie(self):
        facts = copy.deepcopy(FACTS)
        facts["roles"][0]["bullets"] = [
            {"id": "r1x", "text": "Built Airflow ETL pipelines in Python and SQL for the data team.", "status": "approved"},
            {"id": "r1y", "text": "Built Airflow ETL pipelines in Python and SQL for 12 teams.", "status": "approved"}]
        facts["variants"] = {}
        sel = resume.select(facts, JD, "Data Engineer", "data-engineer")
        score = {b["id"]: b["score"] for g in sel["experience"] for b in g["bullets"]} | {b["id"]: b["score"] for b in sel["available"]}
        self.assertGreater(score["r1y"], score["r1x"])  # so when space runs out, the numbered line stays
        self.assertNotIn("r1x", bullet_texts(sel))  # and its near copy stays off: one line per result


class RequirementsTest(unittest.TestCase):
    """Lines count for the posting's requirements they show, and the page reports what it covers."""

    def test_lines_that_show_requirements_win_and_the_page_reports_coverage(self):
        from shortlist import rewrite
        got = rewrite.Rewrites()
        got.requirements = [{"id": "R1", "text": "Build ETL pipelines with Airflow", "kind": "required"},
                            {"id": "R2", "text": "Master's degree in a quantitative field", "kind": "required"},
                            {"id": "R3", "text": "Run controlled experiments", "kind": "preferred"}]
        got.shows = {"r1a": ["R1"], "nope": ["R1"], "r1b": ["R9"]}  # an unknown point and an unknown requirement are ignored
        sel = resume.select(FACTS, JD, "Data Engineer", "data-engineer", rewrites=got)
        report = {r["id"]: r["by"] for r in sel["requirements"]}
        self.assertEqual(report["R1"], ["r1a"])
        self.assertEqual(report["R2"], ["education"])  # a degree requirement is shown by Education
        self.assertEqual(report["R3"], [])  # a gap: reported, never written in
        plain = resume.select(FACTS, JD, "Data Engineer", "data-engineer")
        score = lambda sel, bid: next(b["score"] for g in sel["experience"] for b in g["bullets"] if b["id"] == bid)
        self.assertEqual(round(score(sel, "r1a") - score(plain, "r1a"), 3), resume.REQ_WEIGHT["required"] * 1.25)  # listed first: a quarter more
        self.assertEqual(plain["requirements"], [])


class RequiredEverywhereTest(unittest.TestCase):
    """A required requirement your work shows goes all over the page: summary, experience and projects."""

    def test_a_required_one_shown_in_experience_still_pulls_a_project_line_that_shows_it(self):
        from shortlist import rewrite
        facts = copy.deepcopy(FACTS)
        facts["projects"] = [{"id": "p1", "title": "Pipeline Kit", "status": "approved", "bullets": [
            {"id": "p1a", "text": "Built Python pipelines for open data.", "status": "approved"},
            {"id": "p1b", "text": "Built Tableau charts for a class.", "status": "approved"}]}]
        got = rewrite.Rewrites()
        got.requirements = [{"id": "R1", "text": "Build ETL pipelines in Python", "kind": "required"},
                            {"id": "R2", "text": "Bachelor's degree in computer science", "kind": "required"}]
        got.shows = {"r1a": ["R1"], "p1a": ["R1"]}
        got.summary = None
        sel = resume.select(facts, JD, "Data Engineer", "data-engineer", rewrites=got)
        report = {r["id"]: r for r in sel["requirements"]}
        self.assertEqual(report["R1"]["where"], ["experience", "projects", "skills"])  # in both, not covered once and dropped
        self.assertIn("p1a", bullet_texts(sel))
        self.assertEqual(report["R2"]["where"], ["education"])

    def test_the_report_says_when_the_summary_shows_it(self):
        from shortlist import rewrite
        facts = JobSummaryTest.facts()
        got = rewrite.Rewrites()
        got.requirements = [{"id": "R1", "text": "Build ETL pipelines in Python", "kind": "required"}]
        got.shows = {"r1a": ["R1"]}
        opening = resume.summary_opening(facts, "Data Engineer", "data-engineer")
        got.summary = {"text": opening + ", building ETL pipelines in Python and SQL with Airflow for 1M+ rows.", "uses": ["r1a"], "covers": ["R1", "R7"]}
        sel = resume.select(facts, JD, "Data Engineer", "data-engineer", rewrites=got)
        self.assertEqual(sel["summary"]["covers"], ["R1"])  # an id the posting doesn't have is dropped
        self.assertEqual(sel["requirements"][0]["where"], ["summary", "experience", "skills"])  # Python and ETL are on the skills lines too


class DerivedNumbersTest(unittest.TestCase):
    """A number a line proves itself may be stated: how many named tools it lists, how often its schedule runs."""

    def test_counts_and_schedules_are_exact_and_nothing_is_estimated(self):
        self.assertEqual(resume.derived_numbers("Built websites in HTML, CSS and JavaScript."), {"3"})
        self.assertEqual(resume.derived_numbers("Prepared monthly reports on stock, goods received and issued, and sales."), {"12"})  # no miscount
        self.assertEqual(resume.derived_numbers("Presented the system at MIT and at Akamai."), set())
        mine = "Built websites in HTML, CSS and JavaScript for the data team."
        self.assertEqual(resume.rewrite_problems(mine, "Built websites in 3 languages, HTML, CSS and JavaScript, for the data team."), [])
        self.assertTrue(resume.rewrite_problems(mine, "Built 12 websites in HTML, CSS and JavaScript for the data team."))  # not proven


class SuggestionCheckTest(unittest.TestCase):
    """A suggested line says what it adds beyond its place's facts, so you know what you'd be confirming."""

    def test_a_reworded_line_adds_nothing_and_a_new_claim_says_what_it_adds(self):
        self.assertEqual(resume.suggestion_check("Built ETL pipelines in Python and SQL with Airflow for 1M+ rows.", FACTS, "r1"),
                         {"adds": [], "hard": []})
        new = resume.suggestion_check("Built ETL pipelines with Kafka for 5M+ rows.", FACTS, "r1")
        self.assertTrue(any("Kafka" in a for a in new["adds"]) and any("5M+" in a for a in new["adds"]))
        blanks = resume.suggestion_check("Built a [model] in PyTorch on [dataset].", FACTS, "r1")
        self.assertIn("blanks for you to fill", blanks["adds"])
        self.assertTrue(resume.suggestion_check("Built ETL pipelines; fast.", FACTS, "r1")["hard"])
        self.assertTrue(resume.suggestion_check("Built ETL pipelines.", FACTS, "nope")["hard"])


class ResearchRulesTest(unittest.TestCase):
    """Recruiters read recent, relevant work and the first lines most: a tailored page orders and sizes for that."""

    def test_lines_lead_with_required_and_roles_are_sized_by_fit(self):
        from shortlist import rewrite
        facts = copy.deepcopy(FACTS)
        facts["roles"][0]["bullets"] = [
            {"id": "r1x", "text": "Built Tableau dashboards for 500 users.", "status": "approved"},
            {"id": "r1y", "text": "Built ETL pipelines in Python and SQL with Airflow for 1M+ rows.", "status": "approved"}]
        got = rewrite.Rewrites()
        got.requirements = [{"id": "R1", "text": "Build ETL pipelines with Airflow", "kind": "required"},
                            {"id": "R2", "text": "Build dashboards", "kind": "preferred"}]
        got.shows = {"r1y": ["R1"], "r1x": ["R2"]}
        sel = resume.select(facts, JD, "Data Engineer", "data-engineer", rewrites=got)
        r1 = next(g for g in sel["experience"] if g["id"] == "r1")
        self.assertEqual([b["id"] for b in r1["bullets"]], ["r1y", "r1x"])  # the required one leads, though you listed it second
        mine = resume.select(facts, "", "Data Engineer", "data-engineer")  # a base version keeps your order
        self.assertEqual([b["id"] for b in next(g for g in mine["experience"] if g["id"] == "r1")["bullets"]][:1], ["r1x"])


class PinnedLinesTest(unittest.TestCase):
    """A line you pin stays on the page: no role limit, near-copy rule or fit test takes it off."""

    def test_two_pinned_lines_in_a_capped_role_and_a_pinned_near_copy_all_stay(self):
        facts = copy.deepcopy(FACTS)
        facts["roles"][1]["bullets"].append({"id": "r2b", "text": "Presented forecast results to the planning team.", "status": "approved"})
        facts["roles"][0]["bullets"].append({"id": "r1d", "text": "Built ETL pipelines in Python and SQL with Airflow for 1M+ rows each night.",
                                             "status": "approved"})
        plain = resume.select(facts, JD, "Data Engineer", "data-engineer")
        self.assertEqual(len(next(g for g in plain["experience"] if g["id"] == "r2")["bullets"]), 1)  # a distant role: its best line
        sel = resume.select(facts, JD, "Data Engineer", "data-engineer", include={"r2a", "r2b", "r1d"})
        on = bullet_texts(sel)
        self.assertTrue({"r2a", "r2b", "r1d"} <= set(on))  # every pinned line is on the page
        self.assertNotIn("r1a", on)  # its unpinned near copy makes way


class CoverageAdviceTest(unittest.TestCase):
    """Required items first, then preferred: the preview says what to change, naming the lines."""

    def test_it_asks_for_missing_required_and_names_lines_that_only_repeat(self):
        page = {"a": "Built ETL in Python.", "b": "Built ETL in SQL.", "c": "Built ETL with Airflow.", "d": "Built Tableau charts.",
                "e": "Designed internal forms.", "f": "Tracked inventory."}
        groups = {"a": "r1", "b": "r1", "c": "r1", "d": "r1", "e": "r1", "f": "om"}  # f is its role's only line
        shows = {"a": ["R1"], "b": ["R1"], "c": ["R1"], "d": ["R3"]}
        kinds = {"R1": "required", "R2": "required", "R3": "preferred", "R4": "preferred", "R5": "required"}
        report = [{"id": "R1", "text": "Build ETL pipelines", "kind": "required", "by": ["a", "b", "c"], "where": ["experience"]},
                  {"id": "R2", "text": "Use PyTorch", "kind": "required", "by": [], "where": []},
                  {"id": "R3", "text": "Build dashboards", "kind": "preferred", "by": ["d"], "where": ["experience"]},
                  {"id": "R4", "text": "Know dbt", "kind": "preferred", "by": [], "where": []},
                  {"id": "R5", "text": "Bachelor's degree in computer science", "kind": "required", "by": ["education"], "where": ["education"]}]
        texts = " | ".join(a["text"] for a in resume.coverage_advice(report, shows, kinds, page, groups))
        self.assertIn("1 required not on the page: Use PyTorch", texts)
        self.assertNotIn("Bachelor", texts)  # a degree is covered by Education
        self.assertIn("3 lines only repeat what two other lines already show", texts)
        self.assertIn("to make room for: Use PyTorch", texts)
        self.assertIn("1 line shows none of this job's requirements: Designed internal forms.", texts)
        self.assertNotIn("Tracked inventory", texts)  # a role's only line is never named
        self.assertIn("show only preferred items while a required one is missing", texts)
        self.assertNotIn("Know dbt", texts)  # preferred waits until every required item is on the page
        report[1].update(by=["e"], where=["experience"])
        shows["e"] = ["R2"]
        report[0]["by"] = ["a"]
        report[3].update(by=["d"], where=["experience"])
        shows.update(b=["R3"], c=["R3"])
        report[2]["by"] = ["d", "b", "c"]
        self.assertEqual([a["level"] for a in resume.coverage_advice(report, {"a": ["R1"], "d": ["R3", "R4"], "e": ["R2"]},
                                                                     kinds, {"a": page["a"], "d": page["d"], "e": page["e"]}, groups)], ["ok"])


class JobSummaryTest(unittest.TestCase):
    """A summary written for each job: strong, but every fact in it comes from the lines it cites."""

    @staticmethod
    def facts():
        facts = copy.deepcopy(FACTS)
        facts["summaries"][0].pop("text")
        facts["summaries"][0].update(opener="{title} with {years}+ years across data analytics and engineering, building pipelines.",
                                     closer="M.S., State University.")
        return facts

    def opening(self, facts):
        return resume.summary_opening(facts, "Data Engineer", "data-engineer")

    def test_a_summary_from_your_lines_passes_and_anything_else_fails(self):
        facts = self.facts()
        opening = self.opening(facts)
        self.assertTrue(opening.startswith("Data Engineer with ") and opening.endswith("+ years across data analytics and engineering"))
        good = opening + ", building ETL pipelines in Python and SQL with Airflow for 1M+ rows."
        self.assertEqual(resume.summary_problems(good, facts, opening, ["r1a"]), [])
        for bad in (good.replace("1M+", "5M+"),                                     # a number no cited line has
                    good.replace("building", "leading teams building"),            # scope the lines don't claim
                    good.replace("building ETL", "I build ETL"),                   # first person
                    good.replace(", building", "; building"),                      # a semicolon
                    "Data Engineer with 9+ years building ETL pipelines in Python and SQL for 1M+ rows."):  # other years
            with self.subTest(bad):
                self.assertTrue(resume.summary_problems(bad, facts, opening, ["r1a"]))
        self.assertTrue(resume.summary_problems(good, facts, opening, []))  # it must cite the lines it draws on

    def test_a_result_never_moves_to_another_company(self):
        facts = self.facts()
        opening = self.opening(facts)
        moved = opening + ". At Acme, built ETL pipelines in Python and SQL with Airflow for 1M+ rows and raised forecast accuracy from 70% to 90%."
        self.assertTrue(any("credits Acme" in p for p in resume.summary_problems(moved, facts, opening, ["r1a", "r2a"])))
        kept = opening + ". At Acme, built ETL pipelines in Python and SQL with Airflow for 1M+ rows. Raised forecast accuracy from 70% to 90%."
        self.assertEqual(resume.summary_problems(kept, facts, opening, ["r1a", "r2a"]), [])

    def test_the_page_uses_it_only_when_it_passes_and_you_can_pick_yours(self):
        from shortlist import rewrite
        facts = self.facts()
        got = rewrite.Rewrites()
        got.summary = {"text": self.opening(facts) + ", building ETL pipelines in Python and SQL with Airflow for 1M+ rows.", "uses": ["r1a"]}
        sel = resume.select(facts, JD, "Data Engineer", "data-engineer", rewrites=got)
        self.assertEqual(sel["summary"]["version"], "job")
        self.assertTrue(sel["summary"]["text"].endswith("M.S., State University."))
        self.assertEqual(resume.check(facts, sel, 1), [])
        mine = resume.select(facts, JD, "Data Engineer", "data-engineer", rewrites=got, summary_id="s1")
        self.assertNotEqual(mine["summary"].get("version"), "job")  # your own summary when you pick it
        got.summary = dict(got.summary, text=got.summary["text"].replace("1M+", "9M+"))
        self.assertNotEqual(resume.select(facts, JD, "Data Engineer", "data-engineer", rewrites=got)["summary"].get("version"), "job")
        forged = copy.deepcopy(sel)
        forged["summary"]["text"] = forged["summary"]["text"].replace("1M+", "9M+")
        self.assertTrue(resume.check(facts, forged, 1))


class NeverWordsTest(unittest.TestCase):
    """Words in resume_never_words never print: not in a heading, a line, a rewrite or the page."""

    def setUp(self):
        resume.never_words(["contract", "temp"])
        self.addCleanup(resume.never_words, [])

    def test_headings_drop_the_words_and_keep_everything_else(self):
        for raw, printed in [("Data Engineer (Contract)", "Data Engineer"), ("Data Engineer (6-month contract)", "Data Engineer"),
                             ("Temp Data Analyst", "Data Analyst"), ("Contractor Relations Analyst", "Contractor Relations Analyst"),
                             ("Regional Data Alliance (RDA)", "Regional Data Alliance (RDA)")]:
            self.assertEqual(resume.clean_heading(raw), printed)

    def test_none_by_default(self):
        resume.never_words([])
        self.assertEqual(resume.clean_heading("Data Engineer (Contract)"), "Data Engineer (Contract)")
        self.assertEqual(resume.print_problems("<p>Built ETL tools for 40 contract coordinators.</p>"), [])

    def test_a_line_that_says_them_stays_off_unless_a_version_doesnt(self):
        facts = copy.deepcopy(FACTS)
        facts["roles"][0]["title"] = "Data Engineer (Contract)"
        facts["roles"][0]["bullets"].append({"id": "r1v", "text": "Built ETL tools in SQL for 40 temp coordinators.", "status": "approved"})
        sel = resume.select(facts, JD, "Data Engineer", "data-engineer")
        r1 = next(g for g in sel["experience"] if g["id"] == "r1")
        self.assertEqual(r1["role"], "Data Engineer")
        self.assertNotIn("r1v", bullet_texts(sel))
        facts["roles"][0]["bullets"][-1]["alts"] = [{"id": "r1v1", "text": "Built ETL tools in SQL for 40 coordinators.", "status": "approved", "lens": ""}]
        self.assertEqual(bullet_texts(resume.select(facts, JD, "Data Engineer", "data-engineer"))["r1v"], "Built ETL tools in SQL for 40 coordinators.")

    def test_rewrites_and_the_page_are_checked_for_them(self):
        mine = "Built ETL pipelines in Python and SQL with Airflow for 1M+ rows."
        self.assertTrue(any("never goes on your resume" in p for p in resume.rewrite_problems(mine, "Built ETL pipelines on contract in Python and SQL with Airflow for 1M+ rows.")))
        self.assertTrue(resume.print_problems("<h2>Experience</h2><p>Acme | Data Engineer, Contract</p>"))
        self.assertEqual(resume.print_problems("<p>Built contractor tools for a temperature team.</p>"), [])

    def test_the_rules_name_the_words_only_when_set(self):
        from shortlist import rewrite
        self.assertIn("Never use the words contract or temp.", rewrite._rules(rewrite.RULES))
        resume.never_words([])
        self.assertNotIn("<<", rewrite._rules(rewrite.RULES) + rewrite._rules(rewrite.SUMMARY_RULES) + rewrite._rules(rewrite.SUGGEST_RULES))
        self.assertNotIn("Never use the words", rewrite._rules(rewrite.RULES))


class LayoutTest(unittest.TestCase):
    def test_tools_line_names_only_what_the_entrys_own_lines_name(self):
        sel = resume.select(FACTS, JD, "Data Engineer", "data-engineer")
        r1 = next(g for g in sel["experience"] if g["id"] == "r1")
        self.assertTrue(set(r1["tech"]) <= {"ETL", "Airflow", "SQL", "Python", "Tableau", "Kafka"} - {"Kafka"})  # r1c is pending
        self.assertEqual(resume.check(FACTS, sel, 1), [])
        bad = copy.deepcopy(sel)
        bad["experience"][0]["tech"] = bad["experience"][0]["tech"] + ["Kubernetes"]
        self.assertTrue(any("tools line" in f for f in resume.check(FACTS, bad, 1)))

    def test_bold_never_changes_the_words(self):
        text = FACTS["roles"][0]["bullets"][0]["text"]
        runs = resume.emphasize(text, ["airflow", "sql", "python"])
        self.assertEqual("".join(x for x, _ in runs), text)
        self.assertTrue(any(bold for _, bold in runs))
        self.assertLessEqual(sum(bold for _, bold in runs), 2)

    def test_a_range_counts_both_numbers(self):
        self.assertEqual(resume._nums("replacing 20-30 minutes"), ["20", "30"])
        self.assertEqual(resume._nums("H-1B filings on EC2 and S3"), [])

    def test_render_is_the_classic_layout(self):
        sel = resume.select(FACTS, JD, "Data Engineer", "data-engineer")
        page = resume.render(FACTS, sel)
        self.assertIn("<div class='name'>SAM EXAMPLE</div>", page)
        self.assertIn("<h2>Summary</h2>", page)  # one run of capitals, so every parser reads the heading
        self.assertIn("Charter", page)
        self.assertNotIn("—", page)


class StuffingTest(unittest.TestCase):
    """A tool a rewrite adds to your wording goes in at most ADDED_TOOL_MAX lines and tools lines of a page."""

    @staticmethod
    def facts():
        facts = copy.deepcopy(FACTS)
        facts["roles"] = [{"id": f"r{i}", "title": "Data Engineer", "org": f"Org {i}", "place": "", "dates": f"20{20 + i} – 20{21 + i}",
                           "status": "approved", "bullets": [{"id": f"r{i}a", "text": f"Built ETL pipelines in Python for team {i}.",
                                                              "status": "approved"}]} for i in range(4)]
        facts["added_skills"] = [{"term": "snowflake", "level": "used", "where": ["r0", "r1", "r2", "r3"], "status": "approved"}]
        return facts

    def test_an_added_tool_goes_in_two_lines_and_two_tools_lines(self):
        facts = self.facts()
        got = rewrite.Rewrites()
        got.requirements = [{"id": "R1", "text": "Experience with Snowflake", "kind": "required"},
                            {"id": "R2", "text": "Build ETL pipelines in Python", "kind": "required"}]
        got.update({f"r{i}a": f"Built ETL pipelines in Python with Snowflake for team {i}." for i in range(4)})
        got.shows = {f"r{i}a": ["R1", "R2"] for i in range(4)}
        sel = resume.select(facts, "Data engineer: Snowflake, Python and ETL pipelines.", "Data Engineer", "data-engineer", rewrites=got)
        texts = bullet_texts(sel)
        self.assertEqual(len(texts), 4)  # every line stays on the page
        self.assertEqual(sum("Snowflake" in t for t in texts.values()), resume.ADDED_TOOL_MAX)
        # a tools line never repeats a tool its own entry's lines already name
        for g in sel["experience"]:
            named = {t for b in g["bullets"] for t in resume.terms(b["text"]) if not t.startswith("theme:")}
            self.assertFalse(resume._item_terms(g["tech"]) & named, g["id"])
        self.assertEqual(resume.check(facts, sel, 1), [])

    def test_a_mention_comes_out_only_when_it_comes_out_cleanly(self):
        strip = resume.strip_tools
        self.assertEqual(strip("Built pipelines with Python and AI coding tools for the team.", ["ai_coding"]),
                         "Built pipelines with Python for the team.")
        self.assertEqual(strip("Integrated LLM features with Python, using AI coding tools.", ["ai_coding"]),
                         "Integrated LLM features with Python.")
        self.assertEqual(strip("Built ETL pipelines on AWS with Snowflake to load data.", ["snowflake"]), "Built ETL pipelines on AWS to load data.")
        self.assertEqual(strip("Loaded data with Snowflake, dbt and Airflow.", ["snowflake"]), "Loaded data with dbt and Airflow.")
        self.assertEqual(strip("Integrated LLM features with Python, JavaScript and AI coding tools.", ["ai_coding"]),
                         "Integrated LLM features with Python and JavaScript.")  # the list stays a list
        self.assertEqual(strip("Maintained a Python pipeline using AI coding tools, with 137 automated tests.", ["ai_coding"]),
                         "Maintained a Python pipeline, with 137 automated tests.")
        self.assertIsNone(strip("Snowflake powered the warehouse.", ["snowflake"]))


class ContextTest(unittest.TestCase):
    """A role states its context once: a later line leaves out what an earlier line of the role already says."""

    def test_only_context_phrases_come_out(self):
        trim = resume.trim_context
        self.assertEqual(trim("Delivered dashboards and forecasting models for a $10M NSF-funded alliance.", ["$10M"]),
                         "Delivered dashboards and forecasting models.")
        self.assertEqual(trim("Analyzed performance data from 300+ institutions for a $10M NSF-funded alliance, building ETL pipelines.",
                              ["$10M", "300+"]), "Analyzed performance data, building ETL pipelines.")
        self.assertIsNone(trim("Processed 1M+ rows with Spark.", ["1M+"]))  # there the number is the result, not context

    def test_the_first_line_keeps_it_and_the_check_verifies_the_rest(self):
        facts = copy.deepcopy(FACTS)
        facts["roles"][0]["bullets"] = [
            {"id": "c1", "text": "Built ETL pipelines in Python and SQL with Airflow for a $10M NSF-funded alliance, cutting run time by 40%.",
             "status": "approved"},
            {"id": "c2", "text": "Built Tableau dashboards for 500 users across a $10M NSF-funded alliance.", "status": "approved"}]
        got = rewrite.Rewrites()
        got.requirements = [{"id": "R1", "text": "Build ETL pipelines with Airflow", "kind": "required"},
                            {"id": "R2", "text": "Build dashboards", "kind": "required"}]
        got.shows = {"c1": ["R1"], "c2": ["R2"]}
        sel = resume.select(facts, JD, "Data Engineer", "data-engineer", rewrites=got)
        texts = bullet_texts(sel)
        self.assertIn("$10M", texts["c1"])
        self.assertEqual(texts["c2"], "Built Tableau dashboards for 500 users.")
        self.assertEqual(resume.check(facts, sel, 1), [])
        bad = copy.deepcopy(sel)
        next(g for g in bad["experience"] if g["id"] == "r1")["bullets"].reverse()  # the line that states it no longer comes first
        self.assertTrue(any("leaves out context" in f for f in resume.check(facts, bad, 1)))


class PartialCoverageTest(unittest.TestCase):
    """A requirement shows fully only when every tool group it asks for is in a line and its years add up."""

    def test_tool_groups(self):
        self.assertEqual(resume.tool_groups("3+ years with Spark, Hive or Presto and streaming like Kafka/Flink"),
                         [{"spark", "hive", "presto"}, {"kafka", "flink"}])
        self.assertEqual(resume.tool_groups("Build ETL pipelines and data models"), [])  # general ideas aren't tools
        self.assertEqual(resume.tool_groups("Reporting tools such as Tableau, Superset and Looker"), [{"tableau", "superset", "looker"}])  # one list
        self.assertEqual(resume.tool_groups("Proficiency in Python and SQL"), [{"python"}, {"sql"}])  # both

    def test_a_group_only_on_the_skills_lines_or_too_few_years_is_partial(self):
        facts = copy.deepcopy(FACTS)
        facts["skills"][0]["items"].append("Kafka")
        got = rewrite.Rewrites()
        got.requirements = [{"id": "R1", "text": "3+ years with Airflow and streaming like Kafka", "kind": "required"},
                            {"id": "R2", "text": "1+ years building ETL pipelines with Airflow", "kind": "required"}]
        got.shows = {"r1a": ["R1", "R2"]}
        sel = resume.select(facts, JD, "Data Engineer", "data-engineer", rewrites=got)
        report = {r["id"]: r for r in sel["requirements"]}
        self.assertEqual(report["R1"]["status"], "partial")
        self.assertIn("Kafka only on your skills lines", report["R1"]["gaps"])
        self.assertIn("your roles show about 2 years with Airflow; it asks 3+", report["R1"]["gaps"])
        self.assertEqual((report["R2"]["status"], report["R2"]["gaps"]), ("full", []))
        self.assertTrue(any("only partly shown" in a["text"] for a in sel["advice"]))

    def test_conditions_of_the_job_are_listed_apart(self):
        self.assertTrue(rewrite.is_logistics("Located near one of the listed engineering hubs"))
        self.assertTrue(rewrite.is_logistics("Must be authorized to work in the US without sponsorship"))
        self.assertFalse(rewrite.is_logistics("Experience with hybrid cloud on AWS"))
        self.assertFalse(rewrite.is_logistics("3+ years with Spark and Kafka"))
        got = rewrite.Rewrites()
        got.requirements = [{"id": "R1", "text": "Build ETL pipelines with Airflow", "kind": "required"},
                            {"id": "R2", "text": "Located near one of our hubs", "kind": "logistics"}]
        got.shows = {"r1a": ["R1"]}
        sel = resume.select(FACTS, JD, "Data Engineer", "data-engineer", rewrites=got)
        self.assertEqual([r["id"] for r in sel["requirements"]], ["R1"])
        self.assertEqual(sel["logistics"], [{"id": "R2", "text": "Located near one of our hubs"}])
        self.assertNotIn("hubs", " ".join(a["text"] for a in sel["advice"]))


class HeaderOnlyTest(unittest.TestCase):
    """A role far from the job keeps one line on a tailored page: a heading and dates alone read as a gap, and
    screeners count a role's years only when a line says what was done there."""

    def test_a_far_role_keeps_its_best_line(self):
        got = rewrite.Rewrites()
        got.requirements = [{"id": "R1", "text": "Build ETL pipelines with Airflow", "kind": "required"}]
        got.shows = {"r1a": ["R1"]}
        sel = resume.select(FACTS, JD, "Data Engineer", "data-engineer", rewrites=got)
        r2 = next(g for g in sel["experience"] if g["id"] == "r2")
        self.assertEqual(len(r2["bullets"]), 1)
        self.assertEqual(resume.check(FACTS, sel, 1), [])
        self.assertIn("Research Assistant", resume.render(FACTS, sel))
        pinned = resume.select(FACTS, JD, "Data Engineer", "data-engineer", include={"r2a"}, rewrites=got)
        self.assertEqual(len(next(g for g in pinned["experience"] if g["id"] == "r2")["bullets"]), 1)

    def test_a_far_role_keeps_one_line_even_when_another_line_shows_its_requirement(self):
        got = rewrite.Rewrites()
        got.requirements = [{"id": "R1", "text": "Build ETL pipelines with Airflow", "kind": "required"},
                            {"id": "R2", "text": "Improve forecast accuracy", "kind": "required"}]
        got.shows = {"r1a": ["R1", "R2"], "r2a": ["R2"]}
        sel = resume.select(FACTS, JD, "Data Engineer", "data-engineer", rewrites=got)
        self.assertIn("r2a", bullet_texts(sel))

    def test_a_current_role_or_one_of_the_jobs_kind_always_keeps_a_line(self):
        got = rewrite.Rewrites()
        got.requirements = [{"id": "R1", "text": "Build ETL pipelines with Airflow", "kind": "required"}]
        got.shows = {"r1a": ["R1"]}
        facts = copy.deepcopy(FACTS)
        facts["roles"][1]["dates"] = "2025 – Present"
        sel = resume.select(facts, JD, "Data Engineer", "data-engineer", rewrites=got)
        self.assertIn("r2a", bullet_texts(sel))  # a current role
        facts = copy.deepcopy(FACTS)
        facts["roles"][1]["title"] = "Data Analyst"
        sel = resume.select(facts, JD, "Senior Data Analyst", "data-analyst", rewrites=got)
        self.assertIn("r2a", bullet_texts(sel))  # the job's kind: an analyst role for an analyst job

class KeywordTest(unittest.TestCase):
    """The posting's keywords: ranked, matched as the posting writes them, and put on the page where they're yours."""

    def test_forms_and_matching(self):
        self.assertEqual(resume.keyword_forms("Amazon Web Services (AWS)"), ["amazon web services", "amazon web services aws"])
        self.assertEqual(resume.case_acronyms("Amazon Web Services (AWS)"), ["AWS"])  # matched only in capitals
        self.assertEqual(resume.keyword_shown("Amazon Web Services (AWS)", ["Built ETL on AWS with S3."]), "exact")
        self.assertIn("gcp", resume.keyword_forms("AWS/GCP"))
        self.assertEqual(resume.keyword_forms("CI/CD"), ["ci/cd"])  # one practice, not a choice
        self.assertEqual(resume.keyword_shown("data quality checks", ["Implemented data quality checks on 1M+ rows."]), "exact")
        self.assertEqual(resume.keyword_shown("data models", ["Data & Pipelines : ETL, Data Modeling, SQL"]), "close")
        self.assertEqual(resume.keyword_shown("AWS Glue", ["AWS (S3, Lambda, Glue, EC2)"]), "close")
        self.assertEqual(resume.keyword_shown("Claude Code", ["AI Coding Tools, Anthropic Claude API, FAISS"]), "")  # a tool needs its name

    def test_a_different_word_that_starts_the_same_way_never_matches(self):
        self.assertEqual(resume.keyword_shown("Marketo", ["Built a job-market data pipeline."]), "")
        self.assertEqual(resume.keyword_shown("Agentforce", ["Built 10 AI Agents on AWS."]), "")
        self.assertEqual(resume.keyword_shown("Marketing Automation Platforms (MAPs)", ["Built web maps for wildfire mitigation."]), "")
        self.assertEqual(resume.keyword_shown("Node.js", ["Python, JavaScript, HTML"]), "")  # its own skill, not JavaScript
        self.assertEqual(resume.keyword_shown("communication skills", ["Communicating results to leadership."]), "close")
        self.assertEqual(resume.terms("Econometrics"), {})  # no metrics work inside a longer word

    def test_years_asked_reads_the_low_end_and_spelled_numbers(self):
        for text, need in (("2\u20135 years in data engineering", 2), ("3-5 years", 3), ("One (1) year of experience", 1),
                           ("3 or more years", 3), ("two to four years", 2), ("5+ years with Spark", 5)):
            self.assertEqual(resume.years_asked(text), need, text)
        self.assertIsNone(resume.years_asked("Experience with SQL"))

    def test_skills_items_say_only_skills_you_have(self):
        yours = {"ab_testing", "statistics"}
        themes = {"theme:modeling", "theme:metrics"}
        self.assertTrue(resume.worded_item("Experimentation", yours, themes))  # your A/B testing in the posting's word
        self.assertFalse(resume.worded_item("Statistical Modeling", yours, themes))  # two skills in one claim
        self.assertTrue(resume.worded_item("Statistical Modeling", yours, themes, ["Built statistical models for 300+ schools."]))
        self.assertFalse(resume.worded_item("Mentor", yours, themes | {"theme:leadership"}))  # a trait, not a skill
        self.assertFalse(resume.worded_item("Model Monitoring", yours, themes))  # monitoring is a skill of its own
        self.assertFalse(resume.worded_item("KPI Frameworks", yours, themes))
        self.assertFalse(resume.worded_item("Econometrics", yours, themes))
        kws = resume.job_keywords("Data Scientist", "Strong background in Statistics and forecasting.", [], "", [])
        self.assertIn("Statistics", [k["text"] for k in kws])  # the posting's whole word, never "Statistic"

    def test_ranking_adds_the_tools_the_list_missed_and_never_the_employer(self):
        given = [{"text": "data quality checks", "kind": "skill", "from": "responsibility"}, {"text": "Acme Cloud", "kind": "tool", "from": "required"}]
        reqs = [{"id": "R1", "text": "3+ years with Airflow and SQL", "kind": "required"}]
        kws = resume.job_keywords("Data Engineer", "Build pipelines with Airflow and SQL. Add data quality checks. Nice to have: Tableau.",
                                  given, "Acme", reqs)
        texts = [k["text"] for k in kws]
        self.assertNotIn("Acme Cloud", texts)
        self.assertTrue({"Airflow", "SQL", "Tableau", "data quality checks"} <= set(texts))
        self.assertLess(texts.index("Airflow"), texts.index("Tableau"))  # required before the rest

    def test_the_posting_words_only_carry_in_what_a_line_backs(self):
        may = resume.rewrite_terms("Built a distributed system of 10 AI agents.")
        self.assertTrue({"distribut", "comput"} <= resume.phrase_roots(["distributed computing"], may))
        self.assertNotIn("patient", resume.phrase_roots(["ETL processes for patients"], resume.rewrite_terms("Built ETL pipelines.")))
        self.assertTrue(resume.worded_item("Data Warehouse", {"data_warehouse"}))
        self.assertFalse(resume.worded_item("Data Warehouse for patients", {"data_warehouse"}))
        self.assertFalse(resume.worded_item("Snowflake", {"data_warehouse"}))

    def test_keywords_you_have_go_on_the_page_in_the_postings_words_and_the_report_says_where_each_stands(self):
        facts = copy.deepcopy(FACTS)
        facts["added_skills"] = [{"term": "data_warehouse", "level": "used", "status": "approved"},
                                 {"term": "flink", "level": "no", "status": "approved"}]
        got = rewrite.Rewrites()
        got.requirements = [{"id": "R1", "text": "Build ETL pipelines with Airflow", "kind": "required"}]
        got.shows = {"r1a": ["R1"]}
        got.keywords = [{"text": "data warehouse", "kind": "domain", "from": "required"}, {"text": "Airflow", "kind": "tool", "from": "required"},
                        {"text": "Flink", "kind": "tool", "from": "preferred"}, {"text": "Kubernetes", "kind": "tool", "from": "preferred"}]
        jd = "Build ETL pipelines with Airflow into the data warehouse. Nice to have: Flink and Kubernetes."
        sel = resume.select(facts, jd, "Data Engineer", "data-engineer", rewrites=got)
        items = [it for row in sel["skills"] for it in row["items"]]
        self.assertIn("Data Warehouse", items)  # the posting's words, not "Data Warehousing"
        status = {k["text"]: k["status"] for k in sel["keywords"]}
        self.assertEqual((status["data warehouse"], status["Airflow"], status["Flink"], status["Kubernetes"]),
                         ("on page", "on page", "declined", "ask"))
        self.assertEqual(resume.check(facts, sel, 1), [])

if __name__ == "__main__":
    unittest.main()
