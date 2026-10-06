"""Role families: how to recognize them, and what we know about each.

Priors come from the 2026-10-06 role research (sources in README):
- fit: how well a candidate fits the family overall. A neutral 0.6 here; set your own in
  profile.yaml (`role_overrides: {data_engineer: {fit: 0.9}}`).
- access: how open the role is to 0-2 years of experience (entry-level share, PhD asks).
- typical_wage: H-1B LCA median for the title (Jan-Jun 2026), used when a posting shows no salary.
- socs: SOC codes employers plausibly file this role under (first = most common). Prevailing
  wages differ a lot between them, so eligibility checks every plausible code.
Every prior is overridable in profile.yaml once real outcomes come in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Role:
    key: str
    label: str
    pattern: str  # matched against the lowercased title
    socs: tuple[str, ...]  # plausible SOC codes; the first is the most common
    fit: float
    access: float
    typical_wage: int
    note: str


ROLES: list[Role] = [
    Role("forward_deployed", "Forward-deployed / AI solutions engineer",
         r"forward[- ]deployed|deployment engineer|solutions? engineer|customer engineer|implementation engineer|developer advocate|developer relations|devrel",
         ("15-1252", "41-9031", "15-1211"), 0.6, 0.45, 155_000, "Postings ~8x in a year; H-1B median $155K, none under $100K."),
    Role("analytics_engineer", "Analytics engineer",
         r"analytics engineer",
         ("15-2051", "15-1252", "15-1211"), 0.6, 0.55, 135_000, "dbt and data-modeling heavy."),
    Role("data_engineer", "Data engineer",
         r"data engineer|data platform|data infrastructure|\betl\b|big data|data pipeline|data integration",
         ("15-2051", "15-1243", "15-1252", "15-1242", "15-1299"), 0.6, 0.60, 110_000, "Largest sponsored data title (4,000+ H-1B filings a year)."),
    Role("ai_engineer", "AI / LLM engineer",
         r"\bai\b.*engineer|engineer.*\bai\b|\bllm\b|generative|gen ?ai|applied ai|agentic|ai agents?|agent engineer|prompt engineer|\bai/ml\b",
         ("15-1252", "15-2051", "15-1221"), 0.6, 0.55, 120_000, "Fastest-growing title; postings outpace job seekers."),
    Role("ml_engineer", "ML engineer",
         r"machine learning|\bml\b|mlops|ml platform|ml infrastructure|deep learning",
         ("15-1252", "15-2051", "15-1221"), 0.6, 0.30, 168_000, "Often asks for a PhD (36%) and deep-learning depth."),
    Role("data_scientist", "Data scientist",
         r"data scien|applied scientist|decision scien|research scientist|quantitative analyst|statistician",
         ("15-2051", "15-2041", "15-2031"), 0.6, 0.35, 119_000, "0-2 years is the rarest ask; PhD in ~35% of postings."),
    Role("bi_engineer", "BI developer / engineer",
         r"business intelligence|\bbi\b (developer|engineer|analyst)|tableau|power ?bi|looker",
         ("15-2051", "15-1252", "15-1211"), 0.6, 0.65, 103_100, "Insurers and health systems hire steadily."),
    Role("data_analyst", "Data analyst",
         r"data analyst|analytics analyst|reporting analyst|product analyst|insights analyst|marketing analyst",
         ("15-2051", "15-2041", "15-2031", "13-1161", "15-1211"), 0.6, 0.80, 93_000, "Most open to entry level, but 76% of H-1B filings pay under $100K."),
    Role("business_analyst", "Business / systems analyst",
         r"business analyst|systems analyst|business systems|operations analyst",
         ("15-2051", "13-1111", "15-1211", "13-1161"), 0.6, 0.60, 95_000, "Low pay keeps most filings at wage level I."),
    Role("research_engineer", "Research software / data roles",
         r"research (software|data|engineer|associate|programmer)|computational|bioinformatic|informatics|scientific programmer",
         ("15-1252", "15-2041", "15-1299"), 0.6, 0.55, 95_000, "Universities and nonprofit institutes are cap-exempt: no lottery."),
    Role("consulting", "AI / data consultant",
         r"consultant|consulting|advisory",
         ("15-1211", "13-1111", "15-1252"), 0.6, 0.55, 92_000, "Biggest H-1B filers, but entry consultant pay sits near level I."),
    Role("software_engineer", "Software engineer (backend / data)",
         r"software engineer|software developer|backend|back-end|full[- ]?stack|platform engineer|developer",
         ("15-1252", "15-1299"), 0.6, 0.50, 151_000, "Algorithm-heavy interview loops; 18% of postings open to <=1 year."),
]

ROLE_BY_KEY = {r.key: r for r in ROLES}
_COMPILED = [(r, re.compile(r.pattern)) for r in ROLES]


def role_for_title(title: str) -> Role | None:
    """First matching family wins; the order above puts specific families before broad ones."""
    t = title.lower()
    for role, rx in _COMPILED:
        if rx.search(t):
            return role
    return None
