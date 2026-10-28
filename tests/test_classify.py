import unittest
from datetime import datetime, timezone

from shortlist import score
from shortlist.boards import Posting
from shortlist.classify import has_advanced_degree, is_perm_style, is_us, seniority, years_required


class IsUSTest(unittest.TestCase):
    def test_us_places(self):
        for loc in ["San Jose, CA", "Indianapolis, IN", "Albuquerque, New Mexico", "Portland, OR", "US", "Remote",
                    "Austin, TX or Remote", "San Francisco, CA | New York City, NY"]:
            with self.subTest(loc=loc):
                self.assertTrue(is_us(loc))

    def test_non_us_places(self):
        for loc in ["London, UK", "Bangalore, India", "Remote - Brazil", "San Jose, CR", "Mexico City, Mexico",
                    "France, Remote; Spain, Remote", ""]:
            with self.subTest(loc=loc):
                self.assertFalse(is_us(loc))

    def test_any_us_place_in_a_multi_location_posting(self):
        self.assertTrue(is_us("San Francisco; New York City; London, UK"))
        self.assertTrue(is_us("London; New York; Remote"))
        self.assertFalse(is_us("Delhi, India; Mumbai, India; Bangalore - Remote"))

    def test_title(self):
        # Region words must match whole words: "Capacity" is not APAC.
        self.assertTrue(is_us("San Francisco", title="Capacity Systems Software Engineer"))
        self.assertFalse(is_us("Ontario - Remote", title="AI Support Engineer - Toronto"))


class SeniorityTest(unittest.TestCase):
    def test_hospital_and_university_titles(self):
        # Hospital, pharmacy and university boards are full of titles that match role-family words.
        for title, expected in (("Postdoctoral Research Associate", "postdoc"), ("Post Doc Research Scientist", "postdoc"),
                                ("NVIDIA 2027 Internships: Software Engineering", "internship"),
                                ("Beauty Sales Consultant", "off_profile"), ("Lactation Consultant RN", "off_profile"),
                                ("QM Nurse Consultant", "off_profile"),
                                ("Clinical Research Associate/Coordinator", "off_profile"),
                                ("Adjunct Faculty, Bioinformatics", "off_profile"),
                                ("Equity Research Associate - Banks", "off_profile"), ("Financial Planning Consultant", "off_profile"),
                                ("Executive Partner (CIO Advisory)", "senior")):
            with self.subTest(title=title):
                self.assertEqual(seniority(title), expected)

    def test_data_titles_are_unchanged(self):
        for title, expected in (("Research Data Analyst", "mid"), ("Clinical Data Analyst", "mid"),
                                ("Associate Data Scientist", "entry"), ("Senior Data Engineer", "senior"),
                                ("Software Engineering Intern", "internship"), ("Partner AI Solution Consultant", "mid"),
                                ("Human Resources Data Analyst", "mid")):
            with self.subTest(title=title):
                self.assertEqual(seniority(title), expected)

    def test_a_head_is_senior_wherever_the_word_stands(self):
        for title, expected in (("Head of Data", "senior"), ("Applied AI Head", "senior"),
                                ("WORKFORCE STRATEGY HEAD, ENTERPRISE DATA SCIENCE & ARTIFICIAL INTELLIGENCE (DSAI)", "senior"),
                                ("Data Analyst, Head Start", "mid"), ("Headcount Planning Analyst", "mid")):
            with self.subTest(title=title):
                self.assertEqual(seniority(title), expected)


# Each text maps to the years asked of a candidate with a master's degree
YEARS_CASES = {
    "Minimum Qualifications:\n5 years of experience in data analysis.\n3 years of experience with SQL.": 5,
    "3+ years of Python or R experience, and 5+ years of SQL": 5,
    "5+ years of data engineering experience, including 2+ years with Spark": 5,
    "8+ years of experience in revenue operations, sales strategy, or sales operations with at least 2+ years managing managers": 8,
    "3 or more years of experience with Python": 3,
    "Three (3) years of experience in analytics": 3,
    "0-2 years of experience in analytics": 0,
    "5-7+ years in data science or product analytics roles": 5,
    "Minimum 3 yrs experience in data engineering": 3,
    "You have a total of 1.5+ years of experience as a software engineer.": 2,
    "7+ years of experience (or 5+ years with an advanced degree) in analytics": 5,
    "Bachelor's degree and 3 years' experience OR 7 years' experience in business analysis": 3,
    "Master's degree with 3 years of experience, or Bachelor's degree with 5 years of experience": 3,
    "BS with 5 years of experience or PhD with 1 year of experience": 5,
    "Qualifications\n- 2+ years of SQL experience\nPreferred Qualifications\n- 5+ years of experience": 2,
    "Nice to have\n- 7+ years of experience": None,
    "8+ years of relevant experience preferred": None,
    "Ideally 5+ years of experience": None,
    "At least 4 years of experience; 6+ years is a plus": 4,
    "5+ years of revenue recognition experience (Big 4 Audit experience is a plus)": 5,
    "We have 12 years of experience as a company. For over 20 years we have built tools.": None,
    "References covering the previous 3 years of work. Must be at least 18 years of age.": None,
    "6 months of tractor-trailer experience in the last 3 years": 1,
    "Contract Type: Locum - 3 months with potential of extension. We want an experienced therapist.": None,
}


class YearsRequiredTest(unittest.TestCase):
    def test_researchers_cases(self):
        for text, want in YEARS_CASES.items():
            with self.subTest(text=text):
                self.assertEqual(years_required(text, advanced_degree=True), want)

    def test_what_the_smallest_match_rule_got_wrong(self):
        for text, want in (
            ("Minimum: 5 years of experience in data analysis. 3 years of experience with SQL.", 5),  # was 3
            ("3+ years of Python or R experience, and 5+ years of SQL", 5),  # was 3
            ("3 or more years of experience", 3),  # was none
            ("0-2 years of experience", 0),  # was none
            ("1.5+ years of experience", 2),  # was 5
            ("7+ years of experience (or 5+ with an advanced degree)", 5),  # was 7
            ("We have 12 years of experience as a company.", None),  # was 12
        ):
            with self.subTest(text=text):
                self.assertEqual(years_required(text, advanced_degree=True), want)

    def test_an_alternative_counts_only_when_the_candidate_has_its_degree(self):
        # each text maps to (asked of a bachelor's, which is the default; asked of a master's)
        for text, (bachelors, masters) in {
            "7+ years of experience (or 5+ years with an advanced degree) in analytics": (7, 5),
            "7+ years of experience (or 5+ with an advanced degree)": (7, 5),
            "Master's degree with 3 years of experience, or Bachelor's degree with 5 years of experience": (5, 3),
            "Bachelor's degree and 3 years' experience OR 7 years' experience in business analysis": (3, 3),
            "BS with 5 years of experience or PhD with 1 year of experience": (5, 5),  # a PhD alternative is never met
        }.items():
            with self.subTest(text=text):
                self.assertEqual(years_required(text), bachelors)
                self.assertEqual(years_required(text, advanced_degree=True), masters)

    def test_a_sentence_does_not_end_at_e_g(self):
        text = ("8+ years of relevant work experience with a Bachelor's Degree or at least 5 years of experience with an "
                "Advanced Degree (e.g. Masters, MBA, JD, MD) or 2 years of work experience with a PhD, OR 11+ years of relevant work experience.")
        self.assertEqual(years_required(text, advanced_degree=True), 5)
        self.assertEqual(years_required(text), 8)

    def test_benefits_that_grow_with_tenure_are_not_asks(self):
        for text in ("We offer unlimited PTO and sick time and recognize individuals for 7 years of employment with a paid sabbatical.",
                     "Harvey Holiday: After four years, enjoy a four-week paid sabbatical to rest, travel, or explore new experiences.",
                     "Employees are eligible after six years of service.",
                     "Paid Time Off: 20 days of vacation per year, accruing for the first five years of employment"):
            with self.subTest(text=text):
                self.assertIsNone(years_required(text))

    def test_no_years_named(self):
        for text in ("", "We build data tools for analysts.", "Own the roadmap for our analytics platform."):
            with self.subTest(text=text):
                self.assertIsNone(years_required(text))


class AdvancedDegreeTest(unittest.TestCase):
    def test_the_profile_degree(self):
        for degree in ("masters", "Master's", "MS in Analytics", "M.S.", "MSc", "MBA", "phd", "Ph.D.", "doctorate"):
            with self.subTest(degree=degree):
                self.assertTrue(has_advanced_degree(degree))
        for degree in ("bachelors", "Bachelor's", "BS", "B.A.", "", None, 3):
            with self.subTest(degree=degree):
                self.assertFalse(has_advanced_degree(degree))


class ScoreUsesDegreeTest(unittest.TestCase):
    """score.py hands the profile's degree to the years rule, which sets how many years a master's degree saves."""

    def _score(self, **profile):
        posting = Posting(company="Acme", source="greenhouse", title="Data Analyst", location="Boston, MA",
                          url="https://example.test/jobs/1",
                          text="You have 7+ years of experience (or 5+ years with an advanced degree) in analytics.")
        company = score.Company(name="Acme", boards=[], cap_exempt=True)  # cap-exempt: no wage tables are read
        profile = {"roles": ["data_analyst"], "years_experience": 4.0, "skills": {}, **profile}
        return score.score_posting(posting, company, profile, None, datetime(2026, 10, 27, tzinfo=timezone.utc))

    def test_a_masters_degree_meets_the_shorter_ask(self):
        s = self._score(degree="masters")
        self.assertEqual((s.years_required, s.excluded), (5, None))

    def test_without_a_degree_key_the_longer_ask_applies(self):
        s = self._score()
        self.assertEqual((s.years_required, s.excluded), (7, "asks for 7+ years"))


class PermStyleTest(unittest.TestCase):
    NBSP = "\N{NO-BREAK SPACE}"

    def test_green_card_wording(self):
        for text in (
            "Position also requires the following: 2 years of experience with AWS and Terraform.",
            "Position requires 2 years of experience in the following: Salesforce.",
            f"Position{self.NBSP}also requires the following: 3 years of experience with Python.",
            "Master's degree or foreign equivalent in Computer Science and 2 years of experience in the job offered or a related occupation.",
            "3 years of experience in job offered, software development, or related occupation.",
            f"2 years of experience in the{self.NBSP} job offered or in a Software Engineering related occupation.",
            "In the alternative, the employer will accept a Bachelor's degree plus 7 years of related work experience.",
            "Alternate Education / Experience Requirements: Employer will accept a Master's degree and four years of experience.",
            "Any suitable combination of education, training and experience is acceptable.",
            "Employer will accept any suitable combination of education, training, or experience.",
            "Must also have 3 years of experience in each of the following: SQL; Python.",
            "Must also have four (4) years of experience in each of the following:",
            "Must have at least 1 year of prior work experience in each of the following:",
            "Must also have five (5) years of experience with/in each of the following:",
            "The 3 years of experience must include 3 years of experience with each of the following:",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_perm_style(text))

    def test_ordinary_postings(self):
        for text in (
            "You have 2+ years of experience with Python.",
            "The employer will accept applications until November 1.",
            "Will accept any suitable combination of education, experience, or training.",  # a stock line in ordinary postings
            "We are hiring two coordinators, one for each of the following shifts: 9am to 6pm and 4pm to 1am.",
            "Must have strong skills in each of the following: Python, SQL and Spark.",
            "Bachelor's degree or foreign equivalent in Computer Science and 3 years of experience.",  # H-1B wording, not a PERM ad
            "If you accept the job offered, your start date will be confirmed in writing.",
            "",
        ):
            with self.subTest(text=text):
                self.assertFalse(is_perm_style(text))


if __name__ == "__main__":
    unittest.main()


class SoftwareFieldTest(unittest.TestCase):
    """OPT and STEM OPT need work directly related to the degree: for an analytics degree, general software titles
    stay out, while software titles for data, ML or AI work count as that work."""

    def test_software_titles(self):
        from shortlist.roles import discipline_role, software_field
        for title in ("Software Engineer, AI Teammates Experience", "Software Engineer, Backend",
                      "Software Engineering SMTS / LMTS - AI Core (REMOTE)", "Senior Full Stack Engineer", "iOS Developer"):
            self.assertTrue(software_field(title), title)
        for title in ("Software Engineer, Machine Learning", "Software Engineer, Data Engineering", "Data Engineer",
                      "Software Engineer II - Data Foundations, Core Integrations and Architecture",
                      "Full Stack AI Engineer (Industrial and Physical AI)", "Applied AI Engineer, Startups"):
            self.assertFalse(software_field(title), title)
        self.assertEqual(discipline_role("Software Engineer II - Data Foundations").key, "data_engineer")
        self.assertEqual(discipline_role("Software Engineer, Machine Learning").key, "ml_engineer")


class LabScienceTest(unittest.TestCase):
    def test_lab_scientists_are_another_field(self):
        from shortlist.roles import lab_science, software_field
        self.assertTrue(lab_science("Research Scientist, in vivo Pharmacology-Oncology"))
        self.assertTrue(lab_science("Research Scientist Electrophysiology Neurology"))
        self.assertFalse(lab_science("Computational Biology Scientist"))
        self.assertFalse(lab_science("Data Scientist - Retail Demand Sensing"))
        self.assertTrue(software_field("Software Development Engineer - AI Infrastructure"))
