"""Per-employer H-1B filing counts from h1bdata.info (a public mirror of the DOL
LCA disclosure data; its robots.txt allows crawling).

The site matches the full legal employer name ("ANTHROPIC PBC", not "ANTHROPIC"),
so companies.yaml gives each employer's legal name where the brand differs.
One query per employer per year, at most one request a second, cached under
data/h1bdata/. Rows are kept only when the employer name matches the company
(by its legal-name stem), and each job title is mapped to a role family.
Importing the DOL file directly (`python -m shortlist lca`) takes precedence.
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.parse

import requests

from .lca import norm
from .paths import DATA
from .roles import role_for_title

CACHE = DATA / "h1bdata"
YEARS = (2025, 2026)
USER_AGENT = "shortlist/0.1 (personal job search tool)"
MAX_AGE_S = 7 * 24 * 3600
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_last = 0.0


def _fetch(query: str, year: int) -> list[list[str]]:
    global _last
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{re.sub(r'[^A-Za-z0-9]+', '_', query)}_{year}.json"
    if path.exists() and time.time() - path.stat().st_mtime < MAX_AGE_S:
        return json.loads(path.read_text())
    wait = _last + 1.0 - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last = time.monotonic()
    url = f"https://h1bdata.info/index.php?em={urllib.parse.quote(query)}&job=&city=&year={year}"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    resp.raise_for_status()
    rows = []
    for r in _ROW.findall(resp.text)[1:]:
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in _CELL.findall(r)]
        if len(cells) >= 4:
            rows.append(cells[:6])
    path.write_text(json.dumps(rows))
    return rows


def counts(query: str) -> dict | None:
    """{"filings": n, "by_role": {role: n}, "employers": [...], "median_salary": x} across YEARS.

    None when no employer name matched at all: the site wants the full legal name,
    so an empty result usually means a name mismatch, not zero filings."""
    stem = norm(query)
    by_role: dict[str, int] = {}
    names: dict[str, int] = {}
    salaries: list[float] = []
    total = 0
    for year in YEARS:
        for employer, title, salary, *_ in _fetch(query, year):
            if not norm(employer).startswith(stem):
                continue
            role = role_for_title(title)
            names[employer] = names.get(employer, 0) + 1
            if role is None:
                continue
            total += 1
            by_role[role.key] = by_role.get(role.key, 0) + 1
            try:
                salaries.append(float(salary.replace(",", "")))
            except ValueError:
                pass
    if not names:
        return None
    salaries.sort()
    return {
        "filings": total,
        "by_role": by_role,
        "employers": sorted(names, key=lambda n: -names[n])[:3],
        "median_salary": salaries[len(salaries) // 2] if salaries else None,
        "source": f"h1bdata.info {YEARS[0]}-{YEARS[-1]}",
    }
