"""The claim rules (R3 to R8) and soft flags (S1 to S4) on 30 generic rewrites: each keeps its line's facts or it doesn't."""
import copy
import unittest

from shortlist import resume

# (n, your line, the rewrite, whether it keeps the facts, approved supporting facts, tools confirmed for the role)
CASES = [
    (1, "Built Airflow DAGs to load 40 vendor CSV feeds into Snowflake nightly.",
     "Built Airflow data pipelines that load 40 vendor CSV feeds into Snowflake nightly.", True, (), ()),
    (2, "Wrote SQL checks that flag null and duplicate rows in daily sales tables.",
     "Wrote SQL data quality checks that flag null and duplicate rows in daily sales tables.", True, (), ()),
    (3, "Helped the analytics team migrate 12 Tableau dashboards to Looker.",
     "Helped the analytics team migrate 12 BI dashboards from Tableau to Looker.", True, (), ()),
    (4, "Trained an XGBoost churn model on 2 years of subscription data; AUC 0.81 on holdout.",
     "Trained an XGBoost churn prediction model on 2 years of subscription data, reaching 0.81 AUC on holdout.", True, (), ()),
    (5, "Built a RAG prototype over internal docs using LangChain and pgvector.",
     "Built a retrieval-augmented generation (RAG) prototype over internal docs with LangChain and pgvector.", True, (), ()),
    (6, "Reduced Spark job runtime from 3 hours to 45 minutes by repartitioning skewed joins.",
     "Cut Spark job runtime from 3 hours to 45 minutes by repartitioning skewed joins.", True, (), ()),
    (7, "Our team's recommendation model lifted click-through 8%; I built its feature pipeline.",
     "Built the feature pipeline for the team's recommendation model, which lifted click-through 8%.", True, (), ()),
    (8, "Backfilled 3 years of clickstream events into the lake.",
     "Backfilled 3 years of clickstream events into the data lake with PySpark on EMR.", True, ("Used PySpark on EMR for the backfill.",), ()),
    (9, "Maintain 60 dbt models for the finance reporting mart.", "Maintain the finance reporting mart's 60 dbt models.", True, (), ()),
    (10, "Supported the launch of a demand forecasting service.", "Delivered a demand forecasting service.", False, (), ()),
    (11, "Built 5 Looker dashboards for support ticket metrics.",
     "Built a support analytics data platform with 5 Looker dashboards.", False, (), ()),
    (12, "Built nightly batch ETL pipelines in Airflow.", "Built real-time streaming pipelines in Airflow.", False, (), ()),
    (13, "Part of a 4-person team that cut Snowflake spend 25%.", "Cut Snowflake spend 25%.", False, (), ()),
    (14, "Our team's recommendation model lifted click-through 8%; I built its feature pipeline.",
     "Lifted click-through 8% by building the recommendation model's feature pipeline.", False, (), ()),
    (15, "Worked with ML engineers to deploy a fraud model to production.", "Deployed a fraud detection model to production.", False, (), ()),
    (16, "Built a Python script that deduplicates CRM contacts weekly.",
     "Built a Python script that deduplicates CRM contacts weekly, improving data accuracy for sales.", False, (), ()),
    (17, "Added caching to the feature store API.", "Added caching to the feature store API, enabling real-time model serving.", False, (), ()),
    (18, "Created a churn dashboard reviewed in weekly business meetings.",
     "Created a churn dashboard that shaped retention strategy in weekly business reviews.", False, (), ()),
    (19, "Migrated ETL jobs to Airflow. Pipeline failures fell 40% that quarter.",
     "Reduced pipeline failures 40% by migrating ETL jobs to Airflow.", False, (), ()),
    (20, "Added data contracts for 6 upstream sources; on-call pages dropped from 30 to 12 a month.",
     "Added data contracts for 6 upstream sources, cutting on-call pages from 30 to 12 a month.", False, (), ()),
    (21, "Fine-tuned a BERT ticket-routing classifier; accuracy reached 92% after the relabeling project.",
     "Fine-tuned a BERT ticket-routing classifier to 92% accuracy.", False, (), ()),
    (22, "Building a prototype anomaly detector for payment logs.", "Built an anomaly detector for payment logs.", False, (), ()),
    (23, "Evaluated Delta Lake and Iceberg for the lakehouse migration.", "Migrated the lakehouse to Iceberg.", False, (), ()),
    (24, "Designed a monitoring plan for the credit risk models.", "Deployed monitoring for the credit risk models.", False, (), ()),
    (25, "Built PII masking for customer data in the analytics warehouse.",
     "Built PII masking for patient data in the analytics warehouse.", False, (), ()),
    (26, "Built a sentiment classifier for product reviews.", "Built a sentiment classifier for financial product reviews.", False, (), ()),
    (27, "Wrote Python ETL jobs that load ad spend data into BigQuery.",
     "Wrote PySpark ETL jobs that load ad spend data into BigQuery.", False, (), ("Spark",)),
    (28, "Built a Streamlit demo for the search ranking model.",
     "Built a Streamlit demo for the SageMaker-hosted search ranking model.", False, (), ("SageMaker",)),
    (29, "Cut dashboard load time from 12 s to 4 s.", "Cut dashboard load time by 66%.", False, (), ()),
    (30, "Processed about 2 TB of logs per day.", "Processed 2+ TB of logs per day.", False, (), ()),
]
SOFT = {18: "S3", 21: "S4"}  # may pass the rules, but then this soft flag must catch it


def check(n):
    _, line, text, _, supporting, tools = CASES[n - 1]
    extra = resume._item_terms(list(tools))  # a role's tools as mapped_tools reads them
    return resume.rewrite_problems(line, text, extra, supporting), resume.rewrite_flags(line, text, extra, supporting)


class FactCheckCasesTest(unittest.TestCase):
    """The 30 cases: honest rewrites pass, unfaithful ones fail (or, for 18 and 21, raise their soft flag)."""

    def test_every_case(self):
        for n, line, text, keeps, _, _ in CASES:
            with self.subTest(n=n, text=text):
                why, flags = check(n)
                if keeps:
                    self.assertEqual(why, [])
                elif n in SOFT:
                    self.assertTrue(why or any(f.startswith(SOFT[n]) for f in flags), flags)
                else:
                    self.assertTrue(why)

    def test_each_rule_names_what_it_saw(self):
        expect = {10: "bigger part in the work than your line: delivered",  # R3: helped is not did
                  13: "drops a limit your line states: part of",            # R4: the team's result
                  14: "drops a limit your line states: our",
                  15: "drops a limit your line states: with ml engineers",
                  22: "drops a limit your line states: prototype",
                  23: "drops a limit your line states: evaluated",
                  24: "drops a limit your line states: plan",
                  28: "in use where your line doesn't: hosted",              # R5, though SageMaker is the role's tool
                  25: "kind of data your line doesn't: patients",            # R6
                  26: "kind of data your line doesn't: financial",
                  19: "cause your line doesn't: by migrating",               # R7
                  20: "cause your line doesn't: cutting",
                  16: "cause your line doesn't: improving",
                  11: "more scope or quality than your line: platform",      # R8
                  12: "more scope or quality than your line: real-time"}
        for n, part in expect.items():
            with self.subTest(n=n):
                self.assertTrue(any(part in w for w in check(n)[0]), check(n)[0])
        self.assertIn("it changes your line's tense: building to built", check(22)[0])  # R5: ongoing work doesn't read finished

    def test_soft_flags(self):
        self.assertTrue(any(f.startswith("S4") and "92%" in f for f in check(21)[1]))  # the result moved to another verb
        self.assertTrue(any(f.startswith("S3") and "that shaped" in f for f in check(18)[1]))
        self.assertTrue(any(f.startswith("S2") and "python" in f for f in check(27)[1]))  # PySpark where the line says Python
        self.assertTrue(any(f.startswith("S1") and "delivered" in f for f in check(10)[1]))
        self.assertEqual(check(6)[1], [])  # cut for your reduced is no new claim

    def test_a_role_tool_still_enters_as_the_product_rule_says(self):
        line, text = CASES[26][1], CASES[26][2]
        self.assertTrue(resume.rewrite_problems(line, text, resume._item_terms(["Spark"])))  # Spark is not PySpark
        self.assertEqual(resume.rewrite_problems(line, text, resume._item_terms(["PySpark"])), [])  # confirmed: allowed

    def test_tense_scope_setting_and_link_counts(self):
        self.assertTrue(resume.rewrite_problems("Led and built ETL pipelines in Python for 1M+ rows.",
                                                "Leading and building ETL pipelines in Python for 1M+ rows."))  # finished work reads ongoing
        self.assertEqual(resume.claim_problems("Engineered a recommendation platform on AWS.",
                                               "Engineered a recommendation system on AWS."), [])  # system for platform claims less
        self.assertEqual(resume.claim_problems("Built ETL pipelines for recommendation models.",
                                               "Built ETL pipelines for recommendation systems."), [])  # what the data fed
        self.assertTrue(resume.claim_problems("Built recommendation models.", "Built recommendation systems."))  # what was built
        self.assertEqual(resume.claim_problems(  # one link, however many means it lists
            "Implemented discovery through the registry and messaging through the protocol, enabling agents to find each other.",
            "Enabled agents to find each other through discovery via the registry and messaging through the protocol."), [])
        self.assertEqual(resume._links("Processed 1M+ rows by architecting pipelines, cut by 40% by the team."), [])

    def test_versions_keep_what_you_approved(self):
        line = "Built semantic search on AWS for 500 users."
        alt = {"text": "Built semantic search in production on AWS for 500 users.", "adds": []}
        self.assertTrue(any("went out or is in use" in w for w in resume.faithful(line, alt)))
        self.assertEqual(resume.faithful(line, dict(alt, adds=["theme:production"])), [])  # you approved the production wording


FACTS = {"contact": {"name": "Sam Example", "lines": [], "links": [], "status": "approved"},
         "titles": {"items": ["AI Engineer"], "status": "approved"},
         "summaries": [{"id": "s1", "variant": "ai-engineer", "status": "approved",
                        "opener": "{title} with {years}+ years of data and AI work, building agents.", "closer": "M.S."}],
         "education": [], "skills": [],
         "roles": [{"id": "r1", "title": "AI Engineer", "org": "Acme", "dates": "2021 – 2024", "status": "approved", "bullets": [
             {"id": "r1a", "status": "approved",
              "text": "Built 6 agents that cut response from 20+ minutes to about 25 seconds in a simulated flood."}]}],
         "projects": []}


class SummaryClaimsTest(unittest.TestCase):
    """A job summary keeps the status words and scope of the lines it cites."""

    def test_status_and_scope_carry_over(self):
        facts = copy.deepcopy(FACTS)
        opening = resume.summary_opening(facts, "AI Engineer", "ai-engineer")
        good = opening + ". Built 6 agents that cut response from 20+ minutes to about 25 seconds in a simulated flood."
        self.assertEqual(resume.summary_problems(good, facts, opening, ["r1a"]), [])
        for bad in (good.replace(" in a simulated flood", " in a flood"),        # the simulation dropped
                    good.replace("Built 6 agents", "Deployed 6 agents"),           # deployed: no cited line says so
                    good.replace("Built 6 agents", "Built a platform of 6 agents")):  # more scope
            with self.subTest(bad):
                self.assertTrue(resume.summary_problems(bad, facts, opening, ["r1a"]))


if __name__ == "__main__":
    unittest.main()
