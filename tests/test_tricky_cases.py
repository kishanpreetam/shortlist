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
            # JPMorgan, Nov 2026: 48 characters between "do not" and "sponsorship", then no OPT paperwork
            "We do not offer any type of employment-based immigration sponsorship for this position.": "no",
            "Likewise, JPMorganChase, will not provide any assistance or sign any documentation in support of any other form of immigration sponsorship or benefit including optional practical training (OPT) or curricular practical training (CPT).": "no",
            "Employees who do not opt in to the 401(k) plan are enrolled automatically.": "unknown",  # "opt" isn't OPT
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


class SponsorshipMisreads(unittest.TestCase):
    """Wordings that read wrong until Nov 2026: refusals that read as yes or unknown, citizens-only that read as
    unknown, and open postings that read as no."""

    def test_refusals_read_as_no(self):
        for text in ("No visa sponsorship is available for this role.",  # read as yes
                     "Sponsorship Available: No",  # read as yes
                     "Is visa sponsorship available? No.",  # read as yes
                     "Current and future sponsorship are not available for this position.",  # FIS; read as unknown
                     "Visa sponsorship is unavailable for this role.",
                     "Visa Sponsorship: No"):
            with self.subTest(text=text):
                self.assertEqual(sponsorship_signal(text), "no")

    def test_citizens_only_reads_whole(self):
        # "U.S." no longer ends the sentence, so the citizenship ask and its "must" are read together
        for text in ("Must be a U.S. citizen.", "Applicants must be U.S. citizens or permanent residents."):
            with self.subTest(text=text):
                self.assertEqual(sponsorship_signal(text), "citizen_only")

    def test_open_postings_arent_ruled_out(self):
        self.assertEqual(sponsorship_signal("Whether or not you need visa sponsorship, we welcome you to apply."), "unknown")
        self.assertEqual(sponsorship_signal(
            "Must be a U.S. citizen, permanent resident, or otherwise authorized to work in the U.S."), "unknown")
        self.assertEqual(sponsorship_signal("Must be authorized to work in the U.S.; U.S. citizenship preferred."), "unknown")
        # a "not" in one bullet no longer pairs with "sponsor" in the next
        self.assertEqual(sponsorship_signal("Must not need relocation to work onsite\nVisa sponsorship available"), "yes")
        self.assertEqual(sponsorship_signal("There are no restrictions on visa sponsorship for strong candidates."), "unknown")
        # the agency isn't a citizenship requirement (U.S. Bank and Geotab postings name it for E-Verify)
        self.assertEqual(sponsorship_signal("Employers must verify eligibility through E-Verify, a system operated by "
                                            "U.S. Citizenship and Immigration Services."), "unknown")
        # crypto wallets sponsor gas fees, not visas
        self.assertEqual(sponsorship_signal("Set the paymaster and gas-sponsorship strategy and how it degrades when "
                                            "sponsorship is unavailable."), "unknown")

    def test_a_wrapped_line_is_one_sentence(self):
        self.assertEqual(sponsorship_signal("This role is not eligible for visa\nsponsorship."), "no")
        self.assertEqual(sponsorship_signal("This role is not eligible for visa\xa0 \n sponsorship.\xa0 \n Carvana is"), "no")

    def test_softening_belongs_to_its_own_clause(self):
        # still required: the preference is about the higher clearance
        for text in ("This role requires an active Secret clearance (Top Secret preferred).",
                     "Clearance: Active Secret clearance required; TS/SCI strongly preferred.",
                     "Security Clearance: Active Top Secret clearance strongly preferred; candidates eligible and willing "
                     "to obtain clearance will also be considered."):
            with self.subTest(text=text):
                self.assertEqual(sponsorship_signal(text), "citizen_only")
        # not required: the clause itself, or a bare note right after it, softens it
        for text in ("Clearance: An active U.S. government security clearance is preferred but not required.",
                     "Hold or are eligible for a US security clearance (preferred but not required).",
                     "Security clearance not required, but a plus.",
                     "With eligibility for a US security clearance being a plus.",
                     "U.S. Citizenship Required: No"):
            with self.subTest(text=text):
                self.assertEqual(sponsorship_signal(text), "unknown")


if __name__ == "__main__":
    unittest.main()
