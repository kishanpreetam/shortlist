"""Which employers are startups: young and small.

A startup is a company founded in the last `startup_max_years` years (10 by default) that has at most
`startup_max_employees` people (500 by default), going by shortlist/company_facts.yaml: each employer's founding year
and headcount, with where each number came from. Set either limit in profile.yaml.

Every other employer is established: one that hires through Workday (enterprises do), one established.yaml lists
(public companies and their subsidiaries, decades-old private companies, consultancies, universities, hospitals and
nonprofits), one that is too old or too big, and one whose facts aren't known yet, so no employer is called a
startup on a guess. `missing()` names the employers with postings and no facts, to look up and add.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

HERE = Path(__file__).parent
FACTS_FILE = HERE / "company_facts.yaml"
ESTABLISHED_FILE = HERE / "established.yaml"
MAX_YEARS = 10
MAX_EMPLOYEES = 500

_limits = {"years": MAX_YEARS, "employees": MAX_EMPLOYEES}
_cache: dict[str, object] = {}


def configure(profile: dict) -> None:
    """Take the limits from the profile (startup_max_years, startup_max_employees), or the defaults."""
    _limits["years"] = int(profile.get("startup_max_years") or MAX_YEARS)
    _limits["employees"] = int(profile.get("startup_max_employees") or MAX_EMPLOYEES)


def _load(path: Path, empty):
    key = str(path)
    if key not in _cache:
        _cache[key] = (yaml.safe_load(path.read_text()) if path.exists() else None) or empty
    return _cache[key]


def facts() -> dict[str, dict]:
    return _load(FACTS_FILE, {})


def established() -> set[str]:
    return set(_load(ESTABLISHED_FILE, []))


def is_startup(company: str, board: str = "", today: date | None = None) -> bool:
    return why(company, board, today)[0]


def why(company: str, board: str = "", today: date | None = None) -> tuple[bool, str]:
    """(is it a startup, the reason in a few words), for example (True, "founded 2023, about 120 people")."""
    if board == "Workday":
        return False, "hires through Workday"
    if company in established():
        return False, "on the established list"
    f = facts().get(company) or {}
    founded, people = f.get("founded"), f.get("employees")
    if not isinstance(founded, int) or not isinstance(people, int):
        return False, "founding year or headcount not known yet"
    said = f"founded {founded}, about {people:,} people"
    young = (today or date.today()).year - founded <= _limits["years"]
    return young and people <= _limits["employees"], said


def missing(companies) -> list[str]:
    """The employers among `companies` (names of employers not hiring through Workday) that aren't on the established
    list and have no founding year or headcount yet."""
    known, old = facts(), established()
    return sorted({c for c in companies if c not in old and not (isinstance((known.get(c) or {}).get("founded"), int)
                                                                 and isinstance((known.get(c) or {}).get("employees"), int))})
