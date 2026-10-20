"""Regression tests for the posting text that's easy to misread."""
import unittest

from shortlist.classify import is_contract, seniority, sponsorship_signal, years_required


class SeniorityTest(unittest.TestCase):
    def test_levels_and_titles(self):
        cases = {
            "Senior Data Engineer": "senior", "Staff Software Engineer": "senior", "Lead Data Analyst": "senior",
            "Software Engineer III": "senior", "Engineering Manager, Data": "senior",
            "Principal Applied Scientist": "senior", "Data Engineer II": "mid", "Data Engineer": "mid",
            "Software Engineer I": "entry", "Associate Data Scientist": "entry", "Data Scientist, New Grad": "entry",
            "Machine Learning Engineer, Intern": "internship", "iOS Engineer": "off_profile",
        }
        for title, want in cases.items():
            with self.subTest(title=title):
                self.assertEqual(seniority(title), want)


class YearsTest(unittest.TestCase):
    def test_smallest_sensible_ask(self):
        cases = {
            "3+ years of experience with Python": 3,
            "2-4 years of professional experience in data engineering": 2,
            "5 years experience": 5,
            "At least 1 year of hands-on experience": 1,
            "Bachelor degree and 3 years’ experience": 3,
            "We have 100 years of experience as a company": None,  # about the company, not the role
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(years_required(text), want)


class SponsorshipTest(unittest.TestCase):
    def test_signals(self):
        cases = {
            "We are unable to sponsor visas for this role.": "no",
            "We do not provide sponsorship now or in the future.": "no",
            "Must be authorized to work in the US without the need for current or future sponsorship.": "no",
            "Visa sponsorship is available for this position.": "yes",
            "We sponsor visas.": "yes",
            "Candidates must be U.S. citizens due to ITAR requirements.": "citizen_only",
            "You will partner with an executive sponsor to drive adoption.": "unknown",  # not about visas
            "We will sponsor your security clearance.": "unknown",  # a clearance, not a visa
            "U.S. Citizenship with the ability to obtain and maintain a security clearance.": "citizen_only",
            "The Company may not be able to employ candidates who have United States work authorization related to certain U.S. visa categories, or support future F-1 or H-1B sponsorship.": "no",
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(sponsorship_signal(text), want)


class ClinicalLicenseTest(unittest.TestCase):
    def test_license_requirements(self):
        from shortlist.classify import needs_clinical_license
        self.assertTrue(needs_clinical_license("Requirement: PA or RN license and clinical experience involving direct patient care."))
        self.assertTrue(needs_clinical_license("Must be a registered nurse."))
        self.assertFalse(needs_clinical_license("Build pipelines for clinical trial data with Python and SQL."))


class PermTest(unittest.TestCase):
    def test_green_card_postings(self):
        from shortlist.classify import is_perm_style
        self.assertTrue(is_perm_style("Position also requires the following: 2 years of experience with AWS and Terraform."))
        self.assertTrue(is_perm_style("Position requires 2 years of experience in the following: Salesforce."))
        self.assertFalse(is_perm_style("You have 2+ years of experience with Python."))


class ContractTest(unittest.TestCase):
    def test_contract_from_title_or_type(self):
        self.assertTrue(is_contract("Data Engineer (Contract)"))
        self.assertTrue(is_contract("Data Engineer", "Contract"))
        self.assertFalse(is_contract("Data Engineer", "Full-time"))


if __name__ == "__main__":
    unittest.main()
