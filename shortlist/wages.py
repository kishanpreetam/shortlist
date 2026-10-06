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

from .paths import RAW

WAGE_ZIP = RAW / "OFLC_Wages_2026-27.zip"
HOURS_PER_YEAR = 2080
BOSTON_AREA = "14460"

# DHS's projected single-draw selection odds by wage level (Federal Register, Dec 2025).
SINGLE_DRAW_ODDS = {1: 0.153, 2: 0.306, 3: 0.459, 4: 0.612}
BELOW_LEVEL_1 = 0.0  # a salary under Level I can't support an H-1B at all


def multi_season_odds(level: int, seasons: int) -> float:
    """Chance of being selected at least once across `seasons` lotteries."""
    p = SINGLE_DRAW_ODDS.get(level, BELOW_LEVEL_1)
    return 1 - (1 - p) ** seasons


@lru_cache(maxsize=1)
def _tables() -> tuple[dict, dict, dict]:
    if not WAGE_ZIP.exists():
        raise FileNotFoundError(f"{WAGE_ZIP} missing; run `python -m shortlist data`")
    with zipfile.ZipFile(WAGE_ZIP) as z:
        def rows(name):
            return csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8-sig"))
        alc = {(r["Area"], r["SocCode"]): r for r in rows("ALC_Export.csv")}
        edc = {(r["Area"], r["SocCode"]): r for r in rows("EDC_Export.csv")}
        areas: dict[tuple[str, str], str] = {}  # (state, lowercased area name) -> area code
        for r in rows("Geography.csv"):
            areas[(r["StateAb"], r["AreaName"].lower())] = r["Area"]
    return alc, edc, areas


def area_for(location: str, remote: bool | None, home_area: str = BOSTON_AREA) -> str:
    """Area code for a posting. Remote roles use the candidate's home area."""
    if remote or not location or re.search(r"\bremote\b", location, re.I):
        return home_area
    _, _, areas = _tables()
    m = re.search(r"([A-Za-z .'-]+),\s*([A-Z]{2})\b", location)
    if not m:
        return home_area
    city, state = m.group(1).strip().lower(), m.group(2)
    for (st, name), code in areas.items():
        if st == state and city in name:
            return code
    return home_area


def levels(soc: str, area: str, cap_exempt: bool = False) -> list[float] | None:
    alc, edc, _ = _tables()
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
