"""Government employers, which the queue and the lists leave out when `skip_government: true` is in profile.yaml.

The user asked to skip government jobs (2026-10-29), in the sense bank applications ask about them: an agency at any
level (federal, state, county, city), a public university or community college (a state employer), a government-
sponsored enterprise (Fannie Mae, Freddie Mac, the Federal Home Loan Banks), and the defense and government
contractors whose work is for agencies. Private universities and private nonprofit hospitals aren't government.
Names decide it, so an employer this misses can be added to EMPLOYERS.
"""

from __future__ import annotations

import re

AGENCY = re.compile(r"\b(state of|commonwealth of|county( of| government|$)|city of|town of|village of|department of|government|federal reserve|"
                    r"national laborator(y|ies)|public schools?|school district|municipal|transit authority|port authority|"
                    r"housing authority|veterans affairs|u\.?s\.? (army|navy|air force|marine corps|coast guard))\b", re.I)
PUBLIC_UNIVERSITY = re.compile(
    r"\b(state university|community college|technical college|suny|cuny|city university of new york|"
    r"university of (california|texas|michigan|washington|wisconsin|illinois|minnesota|florida|maryland|massachusetts|virginia|"
    r"north carolina|colorado|arizona|utah|iowa|georgia|kentucky|oregon|connecticut|delaware|new mexico|nebraska|kansas|"
    r"missouri|oklahoma|tennessee|alabama|south carolina|houston|cincinnati|vermont|new hampshire|maine|rhode island|hawaii|"
    r"alaska|idaho|montana|wyoming|nevada|north dakota|south dakota|arkansas|mississippi|louisiana|west virginia|pittsburgh)|"
    r"uc (berkeley|davis|irvine|los angeles|merced|riverside|san diego|san francisco|santa barbara|santa cruz)|ucla|ucsf|ucsd|"
    r"umass|purdue|rutgers|georgia (tech|institute of technology)|ohio state|penn state|pennsylvania state|texas a&m|"
    r"michigan state|indiana university|virginia tech|arizona state|iowa state|oregon state|washington state)\b", re.I)
EMPLOYERS = re.compile(
    r"\b(fannie mae|freddie mac|federal home loan|ginnie mae|sallie mae bank|mitre|booz allen|caci|leidos|saic|mantech|peraton|"
    r"general dynamics information|gdit|northrop grumman|lockheed martin|raytheon|rtx|bae systems|l3harris|accenture federal|"
    r"deloitte government|guidehouse|maximus|battelle|jacobs engineering)\b", re.I)


PRIVATE = re.compile(r"government employees insurance|geico", re.I)  # private companies with a government word in the name


def is_government(company: str) -> str:
    """Why `company` counts as a government employer ("agency", "public university" or "government-backed or
    contractor"), or "" when it doesn't."""
    name = company or ""
    if PRIVATE.search(name):
        return ""
    if PUBLIC_UNIVERSITY.search(name):
        return "public university"
    if EMPLOYERS.search(name):
        return "government-backed or contractor"
    if AGENCY.search(name):
        return "agency"
    return ""
