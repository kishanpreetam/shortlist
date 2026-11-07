"""Prevailing-wage levels and H-1B lottery odds.

Uses the official OFLC wage file (July 2026 to June 2027). A posted salary is
compared with the Level I-IV wages for the role's SOC code in the worksite's
area. Since FY2027 the lottery is wage-weighted, so the level sets the odds.
Cap-exempt employers (universities and their nonprofits) skip the lottery.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from functools import lru_cache

from . import classify
from .paths import RAW

WAGE_ZIP = RAW / "OFLC_Wages_2026-27.zip"
HOURS_PER_YEAR = 2080
BOSTON_AREA = "14460"

# Each state's main metro for a US place whose city no area is named after, such as a suburb ("Milpitas, CA") or a
# Workday site code ("US GA ATL 201 STE 900"): the metro with the most H-1B filings among Shortlist's employers
# (data/h1bdata, 2025-26), except where most such suburbs belong to another (Dallas, Philadelphia, the DC suburbs
# in Maryland, Kansas City).
MAIN_CITY = {
    "AK": "anchorage", "AL": "birmingham", "AR": "little rock", "AZ": "phoenix", "CA": "san jose", "CO": "denver",
    "CT": "hartford", "DC": "washington", "DE": "wilmington", "FL": "tampa", "GA": "atlanta", "HI": "honolulu",
    "IA": "des moines", "ID": "boise", "IL": "chicago", "IN": "indianapolis", "KS": "kansas city", "KY": "louisville",
    "LA": "baton rouge", "MA": "boston", "MD": "washington", "ME": "portland", "MI": "detroit", "MN": "minneapolis",
    "MO": "st. louis", "MS": "jackson", "MT": "bozeman", "NC": "charlotte", "ND": "fargo", "NE": "omaha",
    "NH": "manchester", "NJ": "jersey city", "NM": "albuquerque", "NV": "las vegas", "NY": "new york",
    "OH": "cincinnati", "OK": "oklahoma city", "OR": "portland", "PA": "philadelphia", "RI": "providence",
    "SC": "greenville", "SD": "sioux falls", "TN": "nashville", "TX": "dallas", "UT": "salt lake city",
    "VA": "washington", "VT": "burlington", "WA": "seattle", "WI": "madison", "WV": "charleston", "WY": "cheyenne",
}
WORK_AT_HOME = re.compile(r"\bwork (?:at|from) home\b", re.I)

# DHS's projected single-draw selection odds by wage level (Federal Register, Dec 2025).
SINGLE_DRAW_ODDS = {1: 0.153, 2: 0.306, 3: 0.459, 4: 0.612}
BELOW_LEVEL_1 = 0.0  # a salary under Level I can't support an H-1B at all


def multi_season_odds(level: int, seasons: int) -> float:
    """Chance of being selected at least once across `seasons` lotteries."""
    p = SINGLE_DRAW_ODDS.get(level, BELOW_LEVEL_1)
    return 1 - (1 - p) ** seasons


@lru_cache(maxsize=1)
def _tables() -> tuple[dict, dict, dict, dict]:
    if not WAGE_ZIP.exists():
        raise FileNotFoundError(f"{WAGE_ZIP} missing; run `python -m shortlist data`")
    with zipfile.ZipFile(WAGE_ZIP) as z:
        def rows(name):
            return csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8-sig"))
        alc = {(r["Area"], r["SocCode"]): r for r in rows("ALC_Export.csv")}
        edc = {(r["Area"], r["SocCode"]): r for r in rows("EDC_Export.csv")}
        areas: dict[tuple[str, str], str] = {}  # (state, lowercased area name) -> area code
        states: dict[str, str] = {}  # lowercased state name -> state code
        for r in rows("Geography.csv"):
            areas[(r["StateAb"], r["AreaName"].lower())] = r["Area"]
            states[r["State"].lower()] = r["StateAb"]
    return alc, edc, areas, states


def area_for(location: str, remote: bool | None, home_area: str = BOSTON_AREA) -> str:
    """Area code for a posting. Remote roles use the candidate's home area. "City, ST" uses the area named after the
    city; other US places ("US-VA Richmond - CoStar Tower", "Austin, Texas", "Sunnyvale (US)") use a city an area is
    named after, or else their state's MAIN_CITY. A place outside the US, or with no state or city, uses home_area."""
    if remote or not location or re.search(r"\bremote\b", location, re.I):
        return home_area
    m = re.search(r"([A-Za-z .'-]+),\s*([A-Z]{2})\b", location)
    if m and (code := _named(m.group(1).strip().lower(), m.group(2))):
        return code
    if WORK_AT_HOME.search(location):  # "Work At Home-Delaware; Work At Home-Idaho" is remote work
        return home_area
    for place in classify.PLACE_SEP.split(location):
        if place.strip() and classify.is_us(place) and (code := _place_area(place.strip())):
            return code
    return home_area


def _named(city: str, state: str) -> str | None:
    """The area in `state` whose name holds `city`: "atlanta" in GA is Atlanta-Sandy Springs-Roswell, GA."""
    for (st, name), code in _tables()[2].items():
        if st == state and city in name:
            return code
    return None


@lru_cache(maxsize=1)
def _places() -> tuple[dict[str, str], list[tuple[str, str, str]]]:
    """State names to codes ("new york": "NY") and (city, state, area) for each city an area is named after
    ("San Jose-Sunnyvale-Santa Clara, CA" names san jose, sunnyvale and santa clara), both longest first."""
    _, _, areas, states = _tables()
    cities = {(" ".join(re.findall(r"[a-z]+", city)), st, code)
              for (st, name), code in areas.items() if "nonmetropolitan" not in name
              for city in re.split(r"-+|/", name.rsplit(",", 1)[0])}
    return (dict(sorted(states.items(), key=lambda kv: -len(kv[0]))),
            sorted((c for c in cities if c[0]), key=lambda c: (-len(c[0]), c)))


@lru_cache(maxsize=None)
def _place_area(place: str) -> str | None:
    """A US place's area: the longest city in it that an area is named after (in the place's state when it names one;
    with no state, only a city that names one area), else its state's MAIN_CITY area."""
    states, cities = _places()
    words = " " + " ".join(re.findall(r"[a-z]+", place.lower())) + " "
    names = [n for n in states if f" {n} " in words]  # "west virginia" comes before "virginia"
    codes = [t for t in re.findall(r"\b[A-Z]{2}\b", place) if t in classify.US_STATES]
    state = codes[0] if codes else states[names[0]] if names else None
    # A state's name isn't its city: "USA - Buffalo - New York" is Buffalo.
    found = [code for city, st, code in cities if state in (None, st) and city not in names and f" {city} " in words]
    if found and (state or len(set(found)) == 1):
        return found[0]
    return _named(MAIN_CITY[state], state) if state in MAIN_CITY else None


def levels(soc: str, area: str, cap_exempt: bool = False) -> list[float] | None:
    alc, edc, *_ = _tables()
    row = (edc if cap_exempt else alc).get((area, soc)) or alc.get((area, soc))
    if not row or not row.get("Level1"):
        return None
    try:
        return [float(row[f"Level{i}"]) * HOURS_PER_YEAR for i in range(1, 5)]
    except ValueError:
        return None


def wage_level(salary: float | None, soc: str, area: str, cap_exempt: bool = False) -> int | None:
    """1-4, 0 if below Level I, None if unknown."""
    if not salary:
        return None
    lv = levels(soc, area, cap_exempt)
    if not lv:
        return None
    level = 0
    for i, threshold in enumerate(lv, start=1):
        if salary >= threshold:
            level = i
    return level
