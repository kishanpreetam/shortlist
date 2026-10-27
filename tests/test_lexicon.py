"""Tools learned from job postings (shortlist/lexicon.py) and how the engine takes them in (resume.use_lexicon).

Every posting, read and lexicon here is invented for the test: nothing is read from results/, data/ or profile.yaml.
A test that teaches the engine a tool puts the engine's tables back as it found them.
"""

import copy
import json
import re
import tempfile
import unittest
from pathlib import Path

from shortlist import lexicon, resume

ROWS = {"code", "data", "cloud", "ml", "ai"}  # the skills rows a learned tool can join


def kw(text, kind="tool", where="required"):
    """One keyword of a posting, as Claude's read of it (rewrite.keywords) gives it."""
    return {"text": text, "kind": kind, "from": where}


def reads_of(*keyword_lists):
    """{read id: keywords}, one read per posting."""
    return {f"read-{i}": list(kws) for i, kws in enumerate(keyword_lists, 1)}


def reads_naming(tool, times=3, **how):
    """The reads of `times` postings that each list `tool` among other keywords."""
    return reads_of(*[[kw(tool, **how), kw("Data Pipelines", "skill")] for _ in range(times)])


def corpus(*ads, role="data_engineer", title="Data Engineer"):
    """Postings as board_postings returns them. An ad is (company, what the posting says about the work)."""
    return [{"company": company, "title": title, "role": role, "url": f"https://jobs.example.com/{i}",
             "text": f"{company} is hiring a {title} for its platform team. {work} You will work with analysts and product "
                     "managers, and keep what you build documented and tested. We offer health coverage, a hybrid schedule "
                     "and a learning budget."}
            for i, (company, work) in enumerate(ads, 1)]


def learned_in(lex, text):
    """Which learned tools a text names, with their patterns compiled as the engine compiles them."""
    return {key for key, e in lex["terms"].items() if re.search(e["pattern"], text, re.I)}


def entry(label, *also, case=False, row="data", **more):
    """One learned tool as data/lexicon.json keeps it."""
    names = [label, *also]
    return {"label": label, "spellings": names, "pattern": lexicon.pattern(names, case), "category": row, **more}


def lex_of(**terms):
    return {"version": 1, "terms": terms}


def keyword(found, text):
    return next(k for k in found if k["text"] == text)


# A tool the engine doesn't know, listed with tools it does know by postings from three employers
ORDERS = "You will run our order queues with Python, RabbitMQ and Postgres."
SHIPMENTS = "Our stack is Kafka, RabbitMQ and Snowflake, and you will keep the shipment events flowing."
BROKERS = "Experience with RabbitMQ, Kafka or a comparable message broker is required."
RABBITMQ = (("Acme Retail", ORDERS), ("Beta Logistics", SHIPMENTS), ("Gamma Health", BROKERS))
# The same tool in plain sentences, never listed with a tool the engine knows
UNLISTED = (("Acme Retail", "You will run our order queues on RabbitMQ and tune the consumers."),
            ("Beta Logistics", "Our shipment events pass through RabbitMQ before they reach the warehouse."),
            ("Gamma Health", "Knowing RabbitMQ would help, since we run a comparable message broker."))
# Ray the framework, and X-Ray the imaging technique
RAY = (("Acme Retail", "You will scale training and batch inference with PyTorch, Ray and Spark on our GPU cluster."),
       ("Beta Logistics", "Our stack is Python, Ray and Airflow, so you should be comfortable debugging distributed code."),
       ("Gamma Health", "Experience with Ray, Spark or a similar framework for distributed data processing is a plus."))
XRAY = (("Delta Imaging", "You will build pipelines that process chest X-Ray and CT studies for radiologists."),
        ("Epsilon Dental", "Our platform ingests X-Ray images from clinics and prepares them for review."),
        ("Zeta Clinics", "You will clean the X-Ray metadata that our scanners attach to each study."))
# For each skills row, a tool the engine doesn't know and three engine tools postings list it with
BESIDE = {"data": ("Debezium", ("Kafka", "Spark", "Snowflake")), "ml": ("Kubeflow", ("PyTorch", "TensorFlow", "XGBoost")),
          "ai": ("Qdrant", ("LangChain", "CrewAI", "AutoGen")), "cloud": ("Pulumi", ("Terraform", "Kubernetes", "AWS")),
          "code": ("Vite", ("TypeScript", "React", "Git"))}
WORK = ("Day to day you will build with {a}, {tool} and {b}.",
        "Our stack is {a}, {b}, {c} and {tool}.",
        "Experience with {tool}, {a} or {b} is required.")
EMPLOYERS = ("Acme Retail", "Beta Logistics", "Gamma Health")
# Epic the health-records system, and the lower-case word
HOSPITALS = ("Acme Health", "Beta Medical", "Gamma Clinic", "Delta Hospital", "Epsilon Care")
EPIC = ("You will load patient charts from Epic, SQL Server and Python scripts into our warehouse.",
        "Experience with Epic, SQL or Python reporting is a plus.",
        "Our clinical analysts pull data from Epic, Postgres and Snowflake to track readmissions.")
EPIC_LOWER = (("Zeta Media", "This is an epic opportunity to shape the data platform of a growing company."),
              ("Eta Games", "Our team ships epic stories and the data that tells us how players feel about them."),
              ("Theta Studio", "We tell epic tales, and the data tells us which ones players finish."))
# Your facts, for the tests that tailor a resume
FACTS = {
    "contact": {"name": "Sam Example", "lines": ["Boston, MA", "sam@example.com"], "links": [], "status": "approved"},
    "titles": {"items": ["Data Engineer", "Data Analyst"], "status": "approved"},
    "summaries": [{"id": "s1", "variant": "data-engineer", "text": "Data engineer who builds ETL pipelines in Python and SQL.", "status": "approved"}],
    "education": [{"school": "State University", "degree": "M.S. Data Science", "date": "2025", "status": "approved"}],
    "skills": [{"id": "sk_data", "label": "Data", "items": ["ETL", "Airflow", "SQL"], "status": "approved"},
               {"id": "sk_ml", "label": "ML", "items": ["Forecasting", "Tableau"], "status": "approved"}],
    "roles": [{"id": "r1", "title": "Data Engineer", "org": "Acme", "place": "", "dates": "2023 - 2024", "status": "approved", "bullets": [
        {"id": "r1a", "text": "Built ETL pipelines in Python and SQL with Airflow for 1M+ rows.", "status": "approved"},
        {"id": "r1b", "text": "Moved order events through RabbitMQ queues for 200K orders a day.", "status": "approved"}]}],
    "projects": [], "variants": {},
}
JD = "We need a data engineer with SQL and Airflow to build ETL pipelines. Experience with RabbitMQ is required."
JD_EPIC = "We need a data engineer with SQL and Airflow to build ETL pipelines. Experience with Epic Clarity is required."


class EngineTest(unittest.TestCase):
    """Starts from an engine that knows only its own tools, and leaves its tables as they were found."""

    def setUp(self):
        was = dict(resume.LEARNED)
        self.addCleanup(resume.use_lexicon, {"terms": was} if was else None)
        resume.use_lexicon(None)


class FixtureTest(EngineTest):
    """The tests below invent tools for the engine to learn. If it comes to know one on its own, pick another."""

    def test_the_tools_these_tests_teach_are_not_in_the_engines_own_list(self):
        tools = {"RabbitMQ", "nCino", "Matillion", "OpenTelemetry", "Great Expectations", "Ray", "Access", "Epic", "Open Telemetry", "Open-Telemetry"}
        tools |= {tool for tool, _ in BESIDE.values()}
        for tool in sorted(tools):
            with self.subTest(tool):
                self.assertEqual(resume.keyword_terms(tool, learned=False), set(), f"{tool} is now one of the engine's own tools")

    def test_a_postings_frame_names_none_of_the_engines_tools(self):
        (post,) = corpus(("Acme Retail", ""))
        self.assertEqual({t for t in resume.terms(post["text"]) if not t.startswith("theme:")}, set())


class SpellingsTest(unittest.TestCase):
    def test_a_full_form_and_its_acronym_are_two_names(self):
        self.assertEqual(lexicon.spellings("Amazon Simple Queue Service (SQS)"), ["Amazon Simple Queue Service", "SQS"])

    def test_each_side_of_a_choice_is_a_name(self):
        self.assertEqual(lexicon.spellings("Celery/RabbitMQ"), ["Celery", "RabbitMQ"])

    def test_a_name_with_a_slash_in_it_stays_whole(self):
        for name in ("PL/SQL", "CI/CD", "TCP/IP"):
            with self.subTest(name):
                self.assertEqual(lexicon.spellings(name), [name])

    def test_a_plain_name_is_itself_with_its_spaces_tidied(self):
        self.assertEqual(lexicon.spellings("RabbitMQ"), ["RabbitMQ"])
        self.assertEqual(lexicon.spellings("  Apache   Kafka "), ["Apache Kafka"])

    def test_a_tool_has_one_key_however_it_is_spaced_or_cased(self):
        self.assertEqual(lexicon.key_of("Power Point"), lexicon.key_of("PowerPoint"))
        self.assertEqual(lexicon.key_of("RabbitMQ"), lexicon.key_of("rabbit-mq"))


class ToolLikeTest(unittest.TestCase):
    def test_names_that_read_as_a_tool(self):
        for name in ("RabbitMQ", "nCino", "Infrastructure as Code", "OpenTelemetry", "Great Expectations", "Neo4j",
                     "Amazon Simple Queue Service"):
            with self.subTest(name):
                self.assertTrue(lexicon.tool_like(name))

    def test_kinds_of_software_application_systems_and_phrases_are_not_tools(self):
        for name in ("plugins", "solver packages", "CRM", "SDK", "Workday", "Greenhouse", "837", "", "message queues",
                     "Amazon Simple Queue Service Standard"):
            with self.subTest(name):
                self.assertFalse(lexicon.tool_like(name))


class PatternTest(unittest.TestCase):
    def test_a_name_matches_as_a_whole_word_never_in_or_after_another(self):
        for name in ("C++ Builder", "Node.js", "Neo4j", "F#", "Q&A Studio", "Apache Pulsar", "Open Telemetry"):
            for case in (False, True):
                with self.subTest(name, case=case):
                    rx = re.compile(lexicon.pattern([name], case), re.I)  # as the engine compiles it
                    self.assertTrue(rx.search(f"Experience with {name} is required."))
                    self.assertIsNone(rx.search(f"Experience with pre-{name} is required."), "the end of a hyphenated word")
                    self.assertIsNone(rx.search(f"Experience with {name}ish things is required."), "the start of a longer word")

    def test_spaces_and_hyphens_in_a_name_stand_for_each_other(self):
        rx = re.compile(lexicon.pattern(["Open Telemetry"], False), re.I)
        for written in ("Open Telemetry", "Open-Telemetry", "OpenTelemetry", "open telemetry"):
            with self.subTest(written):
                self.assertTrue(rx.search(f"We use {written} daily."))

    def test_case_keeps_a_names_capitals(self):
        pattern = lexicon.pattern(["Ray"], True)
        self.assertTrue(pattern.startswith("(?-i:"))
        rx = re.compile(pattern, re.I)  # the engine's flag; the group still holds the capitals
        self.assertTrue(rx.search("Scale training with Ray."))
        self.assertIsNone(rx.search("We render with ray tracing."))
        self.assertTrue(re.search(lexicon.pattern(["Ray"], False), "We render with ray tracing.", re.I))


class CandidatesTest(unittest.TestCase):
    def test_a_tool_the_engine_does_not_know_is_a_candidate(self):
        self.assertEqual(set(lexicon.candidates(reads_of([kw("RabbitMQ"), kw("nCino"), kw("Python")]))), {"rabbitmq", "ncino"})

    def test_a_tool_the_engine_knows_is_not(self):
        # Python by its pattern, R and Go as a keyword that is exactly the skill's name, Version Control as a phrase for Git
        known = reads_of([kw("Python"), kw("R"), kw("Go"), kw("Version Control"), kw("Apache Airflow")])
        self.assertEqual(lexicon.candidates(known), {})

    def test_only_tools_are_candidates(self):
        for kind in ("skill", "domain", "education", "soft"):
            with self.subTest(kind):
                self.assertEqual(lexicon.candidates(reads_of([kw("RabbitMQ", kind), kw("OpenTelemetry", kind)])), {})

    def test_a_name_that_does_not_read_as_a_tool_is_left_out(self):
        self.assertEqual(lexicon.candidates(reads_of([kw(name) for name in ("Workday", "CRM", "SDK", "plugins", "837")])), {})

    def test_a_choice_names_each_tool(self):
        self.assertEqual(set(lexicon.candidates(reads_of([kw("Celery/RabbitMQ")]))), {"celery", "rabbitmq"})
        found = lexicon.candidates(reads_of([kw("Amazon Simple Queue Service (SQS)")]))
        self.assertEqual({name for c in found.values() for name in c["spellings"]}, {"Amazon Simple Queue Service", "SQS"})

    def test_a_choice_with_a_known_side_still_names_the_unknown_one(self):
        self.assertEqual(set(lexicon.candidates(reads_of([kw("Kafka/RabbitMQ")]))), {"rabbitmq"})

    def test_a_full_form_and_its_acronym_are_one_tool_across_reads(self):
        reads = reads_of([kw("Amazon Simple Queue Service (SQS)")], [kw("SQS")], [kw("Amazon Simple Queue Service")])
        (key, found), = lexicon.candidates(reads).items()
        self.assertEqual((key, found["reads"]), ("sqs", 3))
        self.assertEqual(set(found["spellings"]), {"Amazon Simple Queue Service", "SQS"})
        # postings that write either name count for the one tool
        posts = corpus(("Acme Retail", "You will run queues on Python, SQS and Postgres."),
                       ("Beta Logistics", "Experience with Kafka or Amazon Simple Queue Service is required."),
                       ("Gamma Health", "Our stack is Lambda, SQS and DynamoDB."))
        tool = lexicon.learn(reads, posts)["terms"]["sqs"]
        self.assertEqual((tool["postings"], tool["employers"]), (3, 3))

    def test_one_employer_however_its_name_is_written_is_one_employer(self):
        ads = [(name, ORDERS) for name in ("Acme Retail", "Acme Retail, Inc.", "ACME RETAIL")]
        lex = lexicon.learn(reads_naming("RabbitMQ"), corpus(*ads))
        self.assertEqual(lex["terms"], {})
        self.assertIn("1 employers", lex["skipped"]["RabbitMQ"])

    def test_one_tool_in_other_spellings_and_reads_is_one_candidate(self):
        reads = reads_of([kw("RabbitMQ"), kw("RabbitMQ")], [kw("Rabbit MQ")], [kw("RabbitMQ", where="preferred")])
        (key, found), = lexicon.candidates(reads).items()
        self.assertEqual(key, "rabbitmq")
        self.assertEqual(found["reads"], 3)  # twice in one read counts once
        self.assertEqual(dict(found["spellings"]), {"RabbitMQ": 2, "Rabbit MQ": 1})


class LearnTest(unittest.TestCase):
    def test_the_bar_is_three_postings_from_two_employers(self):
        self.assertEqual((lexicon.MIN_POSTINGS, lexicon.MIN_EMPLOYERS), (3, 2), "the fixtures here are sized to this bar")

    def test_a_tool_in_three_postings_from_two_employers_is_learned(self):
        posts = corpus(("Acme Retail", ORDERS), ("Acme Retail", SHIPMENTS), ("Beta Logistics", BROKERS))
        lex = lexicon.learn(reads_naming("RabbitMQ"), posts)
        tool = lex["terms"]["rabbitmq"]
        self.assertEqual(tool["label"], "RabbitMQ")
        self.assertEqual(tool["spellings"], ["RabbitMQ"])
        self.assertEqual((tool["postings"], tool["employers"]), (3, 2))
        self.assertIn(tool["category"], ROWS)
        self.assertEqual(learned_in(lex, "We run RabbitMQ in production."), {"rabbitmq"})  # its pattern finds it
        self.assertEqual(learned_in(lex, "We run Kafka in production."), set())
        self.assertEqual(lex["skipped"], {})
        self.assertEqual((lex["reads"], lex["postings"]), (3, 3))

    def test_three_postings_from_one_employer_are_skipped(self):
        posts = corpus(("Acme Retail", ORDERS), ("Acme Retail", SHIPMENTS), ("Acme Retail", BROKERS))
        lex = lexicon.learn(reads_naming("RabbitMQ"), posts)
        self.assertEqual(lex["terms"], {})
        self.assertIn("employers", lex["skipped"]["RabbitMQ"])

    def test_two_postings_are_skipped(self):
        lex = lexicon.learn(reads_naming("RabbitMQ"), corpus(*RABBITMQ[:2]))
        self.assertEqual(lex["terms"], {})
        self.assertTrue(lex["skipped"]["RabbitMQ"])

    def test_a_tool_no_posting_names_is_skipped(self):
        lex = lexicon.learn(reads_naming("RabbitMQ"), corpus(("Acme Retail", "You will keep the warehouse tables tidy.")))
        self.assertEqual(lex["terms"], {})
        self.assertIn("RabbitMQ", lex["skipped"])

    def test_an_employer_naming_itself_does_not_count(self):
        own = (("Matillion", "You will build connectors for Python, Matillion and Snowflake, the stack our customers use."),
               ("Matillion", "Our customers run Airflow, Matillion and dbt, and you will help them do it faster."))
        acme = ("Acme Retail", "Our nightly loads run on Airflow, Matillion and Snowflake and finish before 6 a.m.")
        reads = reads_naming("Matillion", times=4)
        # three postings name it, but two are the maker's own
        lex = lexicon.learn(reads, corpus(*own, acme))
        self.assertEqual(lex["terms"], {})
        self.assertIn("Matillion", lex["skipped"])
        # two more employers name it: those three count, and the maker's own are left out of the counts
        others = (("Beta Logistics", "We schedule Python, Matillion and Snowflake jobs for every source system."),
                  ("Gamma Health", "Experience with Matillion, Airflow or a similar ELT stack is required."))
        tool = lexicon.learn(reads, corpus(*own, acme, *others))["terms"]["matillion"]
        self.assertEqual((tool["postings"], tool["employers"]), (3, 3))

    def test_an_employer_whose_name_holds_the_tool_never_counts(self):
        # "Matillion Labs" isn't taken out of its postings as just "Matillion", but it is still the maker naming itself
        labs = tuple(("Matillion Labs", work) for work in (
            "You will build connectors for Python, Matillion and Snowflake, the stack our customers use.",
            "Our customers run Airflow, Matillion and dbt, and you will help them do it faster.",
            "Experience with Matillion, SQL or a similar ELT tool is required."))
        acme = ("Acme Retail", "Our nightly loads run on Airflow, Matillion and Snowflake and finish before 6 a.m.")
        lex = lexicon.learn(reads_naming("Matillion", times=4), corpus(*labs, acme))
        self.assertEqual(lex["terms"], {})
        self.assertIn("Matillion", lex["skipped"])
    def test_a_capitalized_plain_word_postings_mostly_write_in_lower_case_is_skipped(self):
        in_caps = (("Delta Bank", "Legacy reports live in Excel, Access and SQL Server today, and you will migrate them to the warehouse."),
                   ("Epsilon Dental", "Migrate departmental Access, Excel and Postgres databases to a cloud database."),
                   ("Zeta Insurance", "Retire our old Access, Excel and VBA tools and rebuild them as web apps."))
        in_lower = (("Acme Retail", "You will give analysts access to clean, documented tables and manage who has access to each schema."),
                    ("Beta Logistics", "Strong SQL skills and read-only access to production data come with the job."),
                    ("Gamma Health", "You will have access to our warehouse and a modern data stack."),
                    ("Eta Energy", "Role-based access control and audit logging are part of every project."),
                    ("Theta Media", "You will request access for new teammates and review who can access what."))
        reads = reads_naming("Access", times=4)
        lex = lexicon.learn(reads, corpus(*in_caps, *in_lower))
        self.assertEqual(lex["terms"], {})
        self.assertIn("plain word", lex["skipped"]["Access"])
        # with no lower-case use in the postings the same capitalized word is a tool: lower case is what makes it a word
        self.assertIn("access", lexicon.learn(reads, corpus(*in_caps))["terms"])
    def test_a_name_postings_also_write_in_lower_case_is_matched_only_in_its_capitals(self):
        named = (("Acme Retail", "You will write data quality checks with Python, Great Expectations and SQL, and fail the build when a table drifts."),
                 ("Beta Logistics", "We validate every nightly load with Airflow, Great Expectations and dbt before analysts see it."),
                 ("Gamma Health", "Experience with Great Expectations, SQL or a similar testing framework for data is a plus."))
        said = (("Delta Bank", "We have great expectations for the person who joins us and a clear path to grow."),
                ("Epsilon Dental", "Our leaders have great expectations of this team in its first year."))
        reads = reads_naming("Great Expectations")
        lex = lexicon.learn(reads, corpus(*named, *said))
        tool = lex["terms"]["greatexpectations"]
        self.assertEqual((tool["postings"], tool["employers"]), (3, 3))  # the two lower-case postings are about something else
        self.assertEqual(learned_in(lex, "Experience with Great Expectations is a plus."), {"greatexpectations"})
        self.assertEqual(learned_in(lex, "We have great expectations of you."), set(), "the lower-case use matched")
        # a proper noun keeps its capitals even when no posting writes it in lower case
        only = lexicon.learn(reads, corpus(*named))
        self.assertEqual(learned_in(only, "Experience with GREAT EXPECTATIONS is a plus."), {"greatexpectations"})
        self.assertEqual(learned_in(only, "We have great expectations of you."), set())
    def test_a_word_postings_sometimes_write_in_lower_case_is_never_matched_in_lower_case(self):
        """The module's own example: Epic the health-records system, never an "epic". Either the word is left out as a plain
        word or its pattern keeps its capital, however few of the postings write it in lower case."""
        in_caps = [(HOSPITALS[i % 5], EPIC[i % 3]) for i in range(11)]
        for lower in (1, 2, 3):
            with self.subTest(lower_case_postings=lower):
                lex = lexicon.learn(reads_naming("Epic", times=4), corpus(*in_caps, *EPIC_LOWER[:lower]))
                if "epic" in lex["terms"]:
                    self.assertEqual(learned_in(lex, "Led an epic migration of our data platform."), set(),
                                     f"Epic is learned with the pattern {lex['terms']['epic']['pattern']} although {lower} of {11 + lower} postings "
                                     "write it as 'epic': a word that only a few postings write in lower case stays case-insensitive, "
                                     "so a line of yours that says 'an epic migration' now backs the Epic tool on your resume")
                else:
                    self.assertIn("plain word", lex["skipped"]["Epic"])
    def test_a_name_of_three_letters_or_fewer_is_matched_only_in_its_capitals(self):
        lex = lexicon.learn(reads_naming("Ray"), corpus(*RAY))
        self.assertEqual(learned_in(lex, "Scale training with Ray."), {"ray"})
        self.assertEqual(learned_in(lex, "We render with ray tracing."), set())
        self.assertEqual(learned_in(lex, "RAY OF LIGHT"), set())  # all its capitals, as written

    def test_a_match_right_after_a_hyphen_does_not_count(self):
        lex = lexicon.learn(reads_naming("Ray"), corpus(*RAY, *XRAY))
        tool = lex["terms"]["ray"]
        self.assertEqual((tool["postings"], tool["employers"]), (3, 3), "X-Ray was counted as Ray")
        self.assertEqual(learned_in(lex, "Scale training with Ray."), {"ray"})
        self.assertEqual(learned_in(lex, "Read the chest X-Ray."), set())
        # postings about X-Ray alone teach nothing
        lex = lexicon.learn(reads_naming("Ray"), corpus(*XRAY))
        self.assertEqual(lex["terms"], {})
        self.assertIn("Ray", lex["skipped"])

    def test_the_label_is_the_spelling_postings_write_most(self):
        posts = corpus(("Acme Retail", "Our services emit traces through Langfuse, Kafka and Prometheus."),
                       ("Beta Logistics", "Experience with Langfuse, Python or Docker is a plus."),
                       ("Gamma Health", "We want LangFuse, Docker and Kubernetes skills on every team."))
        tool = lexicon.learn(reads_of(*[[kw("LangFuse")]] * 3), posts)["terms"]["langfuse"]
        self.assertEqual(tool["label"], "Langfuse")  # postings decide, not the read's "LangFuse"

    def test_when_postings_write_two_spellings_equally_the_reads_decide(self):
        # two postings write each spelling, so the postings don't favor one and the reads decide
        posts = corpus(("Acme Retail", "Our Python services emit traces through OpenTelemetry and Kafka, and we alert on them."),
                       ("Acme Retail", "You will add OpenTelemetry, Docker and Kubernetes instrumentation to our order services."),
                       ("Beta Logistics", "Experience with Open Telemetry, Python or Kafka is a plus."),
                       ("Gamma Health", "We want Open Telemetry, Docker and Prometheus skills on every service team."))
        spelled = ("OpenTelemetry", "Open Telemetry")
        for first, most in (spelled, spelled[::-1]):  # the less used spelling is read first, so neither order nor alphabet decides
            with self.subTest(most=most):
                reads = reads_of([kw(first)], *[[kw(most)]] * 3)
                tool = lexicon.learn(reads, posts)["terms"]["opentelemetry"]
                self.assertEqual(tool["label"], most)
                self.assertEqual(sorted(tool["spellings"]), sorted(spelled))
                self.assertEqual(tool["spellings"][0], most)
    def test_a_name_the_reads_hyphenate_or_space_is_found_however_postings_write_it(self):
        posts = corpus(("Acme Retail", "Our Python services emit traces through OpenTelemetry, Kafka and Prometheus."),
                       ("Beta Logistics", "You will add Open Telemetry, Docker and Kubernetes instrumentation to our order services."),
                       ("Gamma Health", "Experience with Open-Telemetry, Python or Kafka is a plus."))
        for read_as in ("Open-Telemetry", "Open Telemetry"):
            with self.subTest(read_as):
                (tool,) = lexicon.learn(reads_naming(read_as), posts)["terms"].values()
                self.assertEqual((tool["postings"], tool["employers"]), (3, 3))
    def test_the_skills_row_follows_the_engine_tools_named_beside_it(self):
        ads, reads = [], {}
        for row, (tool, near) in BESIDE.items():
            ads += [(EMPLOYERS[i], WORK[i].format(tool=tool, a=near[0], b=near[1], c=near[2])) for i in range(3)]
            reads.update({f"{row}-{i}": [kw(tool)] for i in range(3)})
        lex = lexicon.learn(reads, corpus(*ads))
        self.assertEqual({e["label"]: e["category"] for e in lex["terms"].values()}, {tool: row for row, (tool, _) in BESIDE.items()})
    def test_tools_named_everywhere_do_not_pull_every_tool_onto_the_programming_row(self):
        # Python and SQL are in every posting, so beside Debezium they say nothing; the one data tool (Airflow) beside it does
        named = tuple((EMPLOYERS[i], f"Our stack is Python, SQL, Airflow and Debezium, and you will keep the connectors running ({i}).")
                      for i in range(3))
        others = tuple((f"Employer {i}", "You will write Python and SQL to build the weekly reports our managers read.") for i in range(6))
        lex = lexicon.learn(reads_naming("Debezium"), corpus(*named, *others))
        self.assertEqual(lex["terms"]["debezium"]["category"], "data")
    def test_with_no_engine_tool_beside_it_the_row_follows_the_role_family(self):
        # listed only with names the engine doesn't know, so nothing beside it says what the tool is for
        work = ("Our stack is Debezium, Qdrant and Pulumi.", "Experience with Debezium, Qdrant or Pulumi is required.",
                "We use Debezium, Qdrant and Pulumi.")
        for role, title, row in (("data_engineer", "Data Engineer", "data"), ("ml_engineer", "Machine Learning Engineer", "ml"),
                                 ("ai_engineer", "AI Engineer", "ai")):
            with self.subTest(role):
                lex = lexicon.learn(reads_naming("Debezium"), corpus(*zip(EMPLOYERS, work), role=role, title=title))
                self.assertEqual(lex["terms"]["debezium"]["category"], row)

    def test_a_name_no_posting_lists_with_a_tool_the_engine_knows_is_not_learned(self):
        lex = lexicon.learn(reads_naming("RabbitMQ"), corpus(*UNLISTED))
        self.assertEqual(lex["terms"], {})
        self.assertTrue(lex["skipped"]["RabbitMQ"])
        # the same name listed with tools the engine does know is learned
        self.assertIn("rabbitmq", lexicon.learn(reads_naming("RabbitMQ"), corpus(*RABBITMQ))["terms"])


class PreviousTest(unittest.TestCase):
    """What an earlier run learned stays learned: an answer you gave about a tool always keeps its tool."""

    def setUp(self):
        self.reads, self.posts = reads_naming("RabbitMQ"), corpus(*RABBITMQ)
        self.before = lexicon.learn(self.reads, self.posts)
        self.assertIn("rabbitmq", self.before["terms"])

    def test_a_tool_the_new_postings_do_not_name_is_kept(self):
        self.assertEqual(lexicon.learn({}, [], previous=self.before)["terms"], self.before["terms"])

    def test_a_tool_the_new_postings_no_longer_back_is_kept(self):
        later = lexicon.learn(self.reads, self.posts[:1], previous=self.before)  # one posting names it now
        self.assertEqual(later["terms"], self.before["terms"])

    def test_new_tools_join_the_ones_kept(self):
        ncino = (("Acme Retail", "You will extend the loan workflows our branches run on nCino, Salesforce and SQL."),
                 ("Delta Bank", "Experience with nCino, Python or another loan origination system is required."),
                 ("Eta Credit", "Our analysts report on nCino, Tableau and Excel data every week."))
        later = lexicon.learn(reads_naming("nCino"), corpus(*ncino), previous=self.before)
        self.assertEqual(set(later["terms"]), {"rabbitmq", "ncino"})
        self.assertEqual(later["terms"]["rabbitmq"], self.before["terms"]["rabbitmq"])

    def test_a_removed_tool_stays_removed(self):
        removed = copy.deepcopy(self.before)
        removed["terms"]["rabbitmq"]["status"] = "removed"
        for backed, reads, posts in (("not named", {}, []), ("still named", self.reads, self.posts)):
            with self.subTest(backed):
                later = lexicon.learn(reads, posts, previous=removed)
                self.assertEqual(later["terms"]["rabbitmq"]["status"], "removed")

    def test_a_tool_keeps_its_first_date_and_its_old_spellings(self):
        old = copy.deepcopy(self.before)
        old["terms"]["rabbitmq"].update(first_learned="2026-09-01", spellings=["RabbitMQ", "Rabbit MQ"])
        tool = lexicon.learn(self.reads, self.posts, previous=old)["terms"]["rabbitmq"]
        self.assertEqual(tool["first_learned"], "2026-09-01")
        self.assertEqual(tool["spellings"], ["RabbitMQ", "Rabbit MQ"])  # the reads still use RabbitMQ most
        self.assertTrue(re.search(tool["pattern"], "We run Rabbit MQ.", re.I))


class FilesTest(unittest.TestCase):
    """The readers and the saved lexicon, on folders made for the test."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def write(self, name, content):
        (self.dir / name).write_text(content if isinstance(content, str) else json.dumps(content))

    def test_reads_come_from_each_cached_posting_read(self):
        self.write("acme-1.json", {"keywords": [kw("RabbitMQ"), {"text": ""}, "stray", kw("SQL", "tool", "preferred")]})
        self.write("beta-2.json", {"keywords": []})  # nothing read from it
        self.write("gamma-3.json", {"summary": "no keywords"})
        self.write("delta-4.json", "{not json")
        self.write("epsilon-5.json", ["a", "list"])
        self.assertEqual(lexicon.read_keywords(self.dir), {"acme-1": [kw("RabbitMQ"), kw("SQL", "tool", "preferred")]})
        self.assertEqual(lexicon.read_keywords(self.dir / "nowhere"), {})

    def test_board_postings_are_the_ones_in_your_families_once_each(self):
        job = "https://jobs.example.com/{}"
        self.write("acme.json", [
            {"company": "Acme Retail", "title": "Data Engineer", "text": "Build pipelines.", "url": job.format(1)},
            {"company": "Acme Retail", "title": "Barista", "text": "Pour coffee.", "url": job.format(2)},  # in none of your families
            {"company": "Acme Retail", "title": "Data Engineer", "text": "", "url": job.format(3)},  # no text to read
            "not a posting"])
        self.write("beta.json", [
            {"company": "Beta Logistics", "title": "Senior Data Engineer", "text": "The same job again.", "url": job.format(1)},
            {"company": "Beta Logistics", "title": "Machine Learning Engineer", "text": "Train models.", "url": job.format(4)}])
        self.write("broken.json", "{not json")
        posts = lexicon.board_postings(folder=self.dir)
        self.assertEqual([(p["url"], p["role"], p["company"]) for p in posts],
                         [(job.format(1), "data_engineer", "Acme Retail"), (job.format(4), "ml_engineer", "Beta Logistics")])
        self.assertEqual(set(posts[0]), {"company", "title", "text", "url", "role"})
        self.assertEqual([p["url"] for p in lexicon.board_postings(("ml_engineer",), self.dir)], [job.format(4)])
        self.assertEqual(lexicon.board_postings(folder=self.dir / "nowhere"), [])

    def test_a_lexicon_saves_whole_and_loads_back(self):
        lex = lexicon.learn(reads_naming("RabbitMQ"), corpus(*RABBITMQ))
        path = self.dir / "data" / "lexicon.json"  # its folder is made for it
        lexicon.save(lex, path)
        self.assertEqual(lexicon.load(path), lex)
        self.assertEqual([p.name for p in path.parent.iterdir()], ["lexicon.json"], "a half-written copy was left behind")

    def test_a_missing_or_damaged_lexicon_loads_as_nothing(self):
        self.write("damaged.json", "{oops")
        self.write("list.json", ["a", "list"])
        for name in ("missing.json", "damaged.json", "list.json"):
            with self.subTest(name):
                self.assertIsNone(lexicon.load(self.dir / name))


class UseLexiconTest(EngineTest):
    def test_a_learned_tool_is_recognized_in_text(self):
        text = "You will run our order queues on RabbitMQ."
        self.assertNotIn("rabbitmq", resume.terms(text))
        self.assertEqual(resume.keyword_terms("RabbitMQ"), set())
        self.assertEqual(resume.use_lexicon(lex_of(rabbitmq=entry("RabbitMQ"))), 1)  # how many it learned
        self.assertIn("rabbitmq", resume.terms(text))
        self.assertEqual(resume.keyword_terms("RabbitMQ"), {"rabbitmq"})
        self.assertEqual(resume.terms(text)["rabbitmq"], resume.terms("You will run Kafka.")["kafka"], "it weighs like an engine tool")

    def test_it_has_its_label_and_its_skills_row(self):
        resume.use_lexicon(lex_of(rabbitmq=entry("RabbitMQ", row="data"), ncino=entry("nCino", row="ml")))
        self.assertEqual((resume.label("rabbitmq"), resume.item_label("rabbitmq")), ("RabbitMQ", "RabbitMQ"))
        self.assertEqual((resume.label("ncino"), resume.item_label("ncino")), ("nCino", "nCino"))
        self.assertEqual((resume.CATEGORY["rabbitmq"], resume.CATEGORY["ncino"]), ("data", "ml"))
        self.assertTrue({"rabbitmq", "ncino"} <= set(resume.vocabulary()))
        self.assertEqual(set(resume.LEARNED), {"rabbitmq", "ncino"})

    def test_its_label_reads_back_as_the_tool(self):
        # the property tests/test_resume.py checks for the engine's own tools, here for each shape of name a posting gives a tool
        resume.use_lexicon(lex_of(rabbitmq=entry("RabbitMQ"), ncino=entry("nCino"), neo4j=entry("Neo4j"),
                                  iac=entry("Infrastructure as Code"), sqs=entry("Amazon Simple Queue Service", "SQS"),
                                  greatexpectations=entry("Great Expectations", case=True), ray=entry("Ray", case=True)))
        for t in resume.vocabulary():  # the engine's own tools and these
            with self.subTest(t):
                self.assertIn(t, resume._item_terms([resume.item_label(t)]))
                self.assertIn(t, resume.CATEGORY)
        self.assertEqual(resume.vocabulary().count("sqs"), 1)

    def test_a_case_sensitive_pattern_matches_only_its_capitals(self):
        resume.use_lexicon(lex_of(ray=entry("Ray", case=True)))
        self.assertIn("ray", resume.terms("Scale training with Ray."))
        self.assertNotIn("ray", resume.terms("We render with ray tracing."))

    def test_the_engines_own_tools_read_the_same_with_it(self):
        text = "Built ETL pipelines in Python and SQL with Airflow on AWS, and reported in Tableau. Go and R are a plus."
        before = resume.terms(text)
        resume.use_lexicon(lex_of(rabbitmq=entry("RabbitMQ")))
        self.assertEqual(resume.terms(text), before)
        self.assertEqual({k: v for k, v in resume.CATEGORY.items() if k != "rabbitmq"}, resume._BUILTIN_CATEGORY)

    def test_none_forgets_what_was_learned(self):
        resume.use_lexicon(lex_of(rabbitmq=entry("RabbitMQ")))
        for nothing in (None, {}, {"terms": {}}, {"terms": None}):
            with self.subTest(nothing):
                resume.use_lexicon(lex_of(rabbitmq=entry("RabbitMQ")))
                self.assertEqual(resume.use_lexicon(nothing), 0)
                self.assertEqual(sorted(resume.vocabulary()), sorted(resume._BUILTIN_TERMS), "exactly the engine's own tools")
                self.assertEqual(resume.LEARNED, {})
                self.assertEqual(resume.CATEGORY, resume._BUILTIN_CATEGORY)
                self.assertNotIn("rabbitmq", resume.terms("You will run RabbitMQ."))
                self.assertEqual(resume.keyword_terms("RabbitMQ"), set())
                self.assertNotEqual(resume.label("rabbitmq"), "RabbitMQ")

    def test_what_the_engine_read_before_it_learned_a_tool_is_read_again(self):
        line = ["Instrumented the order services with OpenTelemetry traces."]
        self.assertEqual(resume.keyword_shown("Open Telemetry", line), "")  # it doesn't know the tool yet, and remembers that
        resume.use_lexicon(lex_of(opentelemetry=entry("Open Telemetry", "OpenTelemetry")))
        self.assertEqual(resume.keyword_shown("Open Telemetry", line), "close", "the engine kept what it read before it learned the tool")
        resume.use_lexicon(None)
        self.assertEqual(resume.keyword_shown("Open Telemetry", line), "", "the engine kept what it read while it knew the tool")

    def test_a_new_lexicon_replaces_the_last(self):
        resume.use_lexicon(lex_of(rabbitmq=entry("RabbitMQ")))
        resume.use_lexicon(lex_of(ncino=entry("nCino")))
        self.assertEqual(set(resume.LEARNED), {"ncino"})
        self.assertNotIn("rabbitmq", resume.vocabulary())
        self.assertNotIn("rabbitmq", resume.CATEGORY)
        self.assertNotIn("rabbitmq", resume.terms("You will run RabbitMQ."))
        self.assertIn("ncino", resume.terms("You will extend nCino."))

    def test_a_key_the_engine_has_keeps_its_own_tool(self):
        self.assertEqual(resume.use_lexicon(lex_of(python=entry("Monty", row="ai"))), 0)
        self.assertEqual(resume.LEARNED, {})
        self.assertEqual(resume.label("python"), "Python")
        self.assertEqual(resume.CATEGORY["python"], resume._BUILTIN_CATEGORY["python"])
        self.assertIn("python", resume.terms("We write Python."))
        self.assertNotIn("python", resume.terms("Monty"))

    def test_a_removed_tool_is_not_loaded(self):
        self.assertEqual(resume.use_lexicon(lex_of(rabbitmq=entry("RabbitMQ", status="removed"))), 0)
        self.assertNotIn("rabbitmq", resume.LEARNED)
        self.assertNotIn("rabbitmq", resume.vocabulary())
        self.assertNotIn("rabbitmq", resume.terms("You will run RabbitMQ."))
        self.assertEqual(resume.keyword_terms("RabbitMQ"), set())

    def test_a_bad_entry_is_skipped_without_raising_and_the_rest_still_load(self):
        lex = lex_of(rabbitmq=entry("RabbitMQ"),
                     broken={"label": "Broken", "pattern": "(unclosed", "category": "data"},
                     no_label={"pattern": "Thing", "category": "data"},
                     no_pattern={"label": "Thing", "category": "data"},
                     not_a_dict="RabbitMQ")
        self.assertEqual(resume.use_lexicon(lex), 1)
        self.assertEqual(set(resume.LEARNED), {"rabbitmq"})
        self.assertEqual(set(resume.vocabulary()) - set(resume._BUILTIN_TERMS), {"rabbitmq"})
        self.assertIn("rabbitmq", resume.terms("You will run RabbitMQ."))

    def test_a_row_the_engine_does_not_have_falls_back_to_data(self):
        for what, e in (("unknown row", entry("RabbitMQ", row="messaging")), ("no row", {"label": "RabbitMQ", "pattern": "RabbitMQ"})):
            with self.subTest(what):
                resume.use_lexicon(lex_of(rabbitmq=e))
                self.assertEqual(resume.CATEGORY["rabbitmq"], "data")

    def test_keyword_terms_can_leave_learned_tools_out(self):
        resume.use_lexicon(lex_of(rabbitmq=entry("RabbitMQ")))
        self.assertEqual(resume.keyword_terms("Kafka and RabbitMQ"), {"kafka", "rabbitmq"})
        self.assertEqual(resume.keyword_terms("Kafka and RabbitMQ", learned=False), {"kafka"})
        self.assertEqual(resume.keyword_terms("RabbitMQ", learned=False), set())
        self.assertEqual(resume.keyword_terms("rabbitmq", learned=False), set())

    def test_a_line_naming_only_a_learned_tool_of_a_bold_row_is_set_in_bold(self):
        # with none of the job's skills in a bullet, its first named tool is bold: the engine's data, cloud, ml and ai tools
        line = "Tuned RabbitMQ consumers for order events."
        plain = [(line, False)]
        self.assertEqual(resume.emphasize(line, []), plain)
        resume.use_lexicon(lex_of(rabbitmq=entry("RabbitMQ", row="data")))
        self.assertEqual(resume.emphasize(line, []), [("Tuned ", False), ("RabbitMQ", True), (" consumers for order events.", False)])
        resume.use_lexicon(lex_of(rabbitmq=entry("RabbitMQ", row="code")))  # a programming tool isn't, as Python isn't
        self.assertEqual(resume.emphasize(line, []), plain)
        resume.use_lexicon(None)
        self.assertEqual(resume.emphasize(line, []), plain)


class KeywordTermsTest(EngineTest):
    def test_a_keyword_that_is_exactly_a_skills_name_names_it(self):
        self.assertEqual(resume.keyword_terms("R"), {"r_lang"})
        self.assertEqual(resume.keyword_terms("Go"), {"go"})

    def test_the_skills_own_patterns_still_name_a_longer_keyword(self):
        self.assertEqual(resume.keyword_terms("R programming"), {"r_lang"})
        self.assertEqual(resume.keyword_terms("Go (Golang)"), {"go"})
        self.assertEqual(resume.keyword_terms("Python and SQL"), {"python", "sql"})

    def test_a_keyword_that_only_starts_like_a_skill_names_nothing(self):
        for text in ("Go-to-market", "R&D", "Research", "Going"):
            with self.subTest(text):
                self.assertEqual(resume.keyword_terms(text), set())

    def test_no_text_names_nothing(self):
        for text in ("", "   ", None):
            with self.subTest(text):
                self.assertEqual(resume.keyword_terms(text), set())

    def test_job_keywords_gives_such_a_keyword_its_term(self):
        post = ("We are hiring a Data Analyst to study survey results for our research team. You will clean the data in SQL, "
                "model it in R and present weekly findings to program managers. Our services are written in Go.")
        for text, term in (("R", "r_lang"), ("Go", "go")):
            with self.subTest(text):
                found = resume.job_keywords("Data Analyst", post, [kw(text)])
                self.assertEqual(keyword(found, text)["terms"], [term])


class MineTest(unittest.TestCase):
    """Names read from every line of every posting: a list that names two of the engine's tools is a list of tools."""

    def ads(self, line):
        return corpus(*[(name, line) for name in EMPLOYERS])

    def test_a_name_listed_beside_two_known_tools_is_mined_and_learned(self):
        posts = self.ads("You will build with Python, Kubeflow and Airflow.")
        self.assertIn("kubeflow", lexicon.mine(posts))
        self.assertIn("kubeflow", lexicon.learn({}, posts)["terms"])  # no read of any posting named it

    def test_a_list_of_topics_is_not_a_list_of_tools(self):
        self.assertEqual(lexicon.mine(self.ads("Strong background in statistics, Econometrica and machine learning.")), {})

    def test_one_known_tool_is_not_a_list(self):
        self.assertEqual(lexicon.mine(self.ads("You will build with Python and Kubeflow.")), {})

    def test_a_vendors_first_word_is_not_a_tool(self):
        posts = self.ads("Experience with Python, Google and Airflow. We run on Google Cloud and Google Kubernetes Engine.")
        self.assertNotIn("google", lexicon.mine(posts))

    def test_a_plain_word_that_opens_a_line_is_not_a_name(self):
        posts = self.ads("Strong, Python and SQL skills.\nWe use Python, Kubeflow and SQL.")
        self.assertEqual(set(lexicon.mine(posts)), {"kubeflow"})


class OwnMatchTest(EngineTest):
    """A learned tool is read only where the name is its own: not inside one of the engine's tools, and not at an
    employer named for it."""

    def test_a_learned_match_inside_an_engine_tool_is_the_engine_tool(self):
        resume.use_lexicon(lex_of(github=entry("GitHub", row="code")))
        self.assertNotIn("github", resume.terms("Built CI on GitHub Actions for every pull request."))
        self.assertIn("ci_cd", resume.terms("Built CI on GitHub Actions for every pull request."))
        self.assertIn("github", resume.terms("Our code lives on GitHub and GitLab."))
        self.assertEqual(resume.keyword_terms("GitHub Actions"), {"ci_cd"})
        g = {"bullets": [{"id": "b", "text": "Shipped releases with GitHub Actions and Docker.", "status": "approved"}]}
        self.assertNotIn("GitHub", resume.tech_items(g, fit=False))  # the tools line names GitHub Actions, not GitHub

    def test_an_employer_named_for_a_learned_tool_does_not_ask_for_it(self):
        resume.use_lexicon(lex_of(epic=entry("Epic", case=True)))
        text = "WHAT MAKES US EPIC? We hire data engineers who love Epic stories."
        self.assertNotIn("epic", resume.terms(resume.without_employer(text, "Epic Games")))
        self.assertIn("epic", resume.terms(resume.without_employer("Load charts from Epic and SQL Server.", "Acme Health")))

    def test_a_slash_inside_one_known_tool_names_no_new_tool(self):
        self.assertEqual(lexicon.candidates(reads_of([kw("Pub/Sub"), kw("CI/CD")])), {})
        self.assertEqual(set(lexicon.candidates(reads_of([kw("Git/GitHub")]))), {"github"})

    def test_learning_reads_a_tool_only_where_the_name_is_its_own(self):
        actions = [(name, "You will ship with Python, GitHub Actions and Docker.") for name in EMPLOYERS]
        lex = lexicon.learn(reads_naming("GitHub"), corpus(*actions))
        self.assertEqual(lex["terms"], {})  # GitHub Actions is the engine's CI/CD, not GitHub
        own = [(name, "Experience with Git, GitHub and Docker is required.") for name in EMPLOYERS]
        self.assertIn("github", lexicon.learn(reads_naming("GitHub"), corpus(*own))["terms"])


class EndToEndTest(EngineTest):
    ADS = (("Acme Retail", "Our services emit traces and metrics through Python, OpenTelemetry and Kafka, and you will keep that data reliable."),
           ("Acme Retail", "You will add OpenTelemetry, Docker and Kubernetes instrumentation to the order pipeline and alert on its signals."),
           ("Beta Logistics", "Experience with OpenTelemetry, Prometheus or a similar tracing standard is required."))
    NEW_POSTING = ("Zeta Insurance is hiring a Platform Data Engineer. Our claims services emit traces and metrics through OpenTelemetry, "
                   "and you will keep that data reliable for analysts. Python and SQL are required, and we offer a hybrid schedule.")

    def learned(self):
        lex = lexicon.learn(reads_naming("OpenTelemetry"), corpus(*self.ADS))
        (key,) = lex["terms"]
        return lex, key

    def test_a_tool_learned_from_postings_reaches_the_next_postings_keywords(self):
        lex, key = self.learned()
        asked = [kw("OpenTelemetry"), kw("Python"), kw("SQL")]
        before = resume.job_keywords("Platform Data Engineer", self.NEW_POSTING, asked)
        self.assertEqual(keyword(before, "OpenTelemetry")["terms"], [], "OpenTelemetry is now one of the engine's own tools; pick another")
        resume.use_lexicon(lex)
        after = resume.job_keywords("Platform Data Engineer", self.NEW_POSTING, asked)
        self.assertEqual(keyword(after, "OpenTelemetry")["terms"], [key])
        self.assertEqual(keyword(after, "Python")["terms"], ["python"])  # the engine's own tools read as before
        self.assertEqual(resume.label(key), "OpenTelemetry")
        self.assertIn(key, resume._item_terms([resume.item_label(key)]))

    def test_a_tool_claude_missed_is_still_found_in_the_posting(self):
        lex, key = self.learned()
        given = [kw("Python")]  # Claude's keywords leave OpenTelemetry out
        before = resume.job_keywords("Platform Data Engineer", self.NEW_POSTING, given)
        self.assertNotIn("OpenTelemetry", [k["text"] for k in before])
        resume.use_lexicon(lex)
        found = keyword(resume.job_keywords("Platform Data Engineer", self.NEW_POSTING, given), "OpenTelemetry")
        self.assertEqual((found["kind"], found["terms"]), ("tool", [key]))

    def test_a_learned_tool_your_lines_do_not_show_is_asked_about_never_added(self):
        without = copy.deepcopy(FACTS)
        without["roles"][0]["bullets"].pop(1)  # the line that names RabbitMQ
        resume.use_lexicon(lex_of(rabbitmq=entry("RabbitMQ", row="data")))
        sel = resume.select(without, JD, "Data Engineer", "data-engineer")
        self.assertEqual(keyword(sel["keywords"], "RabbitMQ")["status"], "ask")
        self.assertIn("RabbitMQ", sel["missing"])
        self.assertNotIn("RabbitMQ", [item for row in sel["skills"] for item in row["items"]])
        self.assertEqual(resume.check(without, sel, 1), [])

    def test_a_lower_case_word_in_your_line_does_not_back_a_learned_tool_named_like_it(self):
        in_caps = [(HOSPITALS[i % 5], EPIC[i % 3]) for i in range(11)]
        lex = lexicon.learn(reads_naming("Epic", times=4), corpus(*in_caps, EPIC_LOWER[0]))  # one posting in twelve says "epic"
        resume.use_lexicon(lex)
        facts = copy.deepcopy(FACTS)
        facts["roles"][0]["bullets"][1]["text"] = "Led an epic migration of our data platform for 40 analysts."
        sel = resume.select(facts, JD_EPIC, "Data Engineer", "data-engineer")
        self.assertNotIn("Epic", [item for row in sel["skills"] for item in row["items"]],
                         f"BUG: your line says 'an epic migration', and with {lex['terms'].get('epic', {}).get('pattern')} as the pattern for the "
                         "tool Epic the engine put Epic on your skills row, a claim none of your lines makes")
        self.assertEqual(resume.check(facts, sel, 1), [])

    def test_a_learned_tool_one_of_your_lines_shows_joins_the_row_it_belongs_on(self):
        for row, joins in (("data", "sk_data"), ("ml", "sk_ml")):
            with self.subTest(row):
                resume.use_lexicon(lex_of(rabbitmq=entry("RabbitMQ", row=row)))
                sel = resume.select(FACTS, JD, "Data Engineer", "data-engineer")
                self.assertEqual(keyword(sel["keywords"], "RabbitMQ")["status"], "on page")
                self.assertNotIn("RabbitMQ", sel["missing"])
                self.assertEqual([x["id"] for x in sel["skills"] if "RabbitMQ" in x["items"]], [joins])
                self.assertEqual(resume.check(FACTS, sel, 1), [])


if __name__ == "__main__":
    unittest.main()
