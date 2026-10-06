"""Finds more employers worth scanning: ones that actually file H-1Bs for your
role families AND publish jobs on a board this tool can read.

1. Pull 2025-26 H-1B filings by job title from h1bdata.info (one query per title per year).
2. Count filings per employer and role family; drop staffing/outsourcing firms,
   whose client-site placements don't qualify for STEM OPT.
3. Match employer names to Greenhouse / Lever / Ashby board ids, then confirm each
   match against the board's own company name (Greenhouse API, or the page title).
Results go to data/discovered.yaml and are scanned on the next `run`.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse

import requests
import yaml

from .lca import norm
from .paths import DATA, RAW
from .roles import role_for_title

TITLES = ["data engineer", "data analyst", "data scientist", "analytics engineer", "business intelligence",
          "machine learning engineer", "ai engineer", "applied scientist", "forward deployed engineer"]
YEARS = (2025, 2026)
OUT = DATA / "discovered.yaml"
CACHE = DATA / "h1bdata_titles"
USER_AGENT = "shortlist/0.1 (personal job search tool)"
STAFFING = re.compile(
    r"compunnel|insight global|tata consultancy|infosys|cognizant|wipro|tech mahindra|hcl america|capgemini|mphasis|"
    r"ltimindtree|larsen|virtusa|hexaware|mindtree|igate|syntel|ust global|zensar|birlasoft|cyient|persistent systems|"
    r"staffing|consultancy services|infotech|it solutions|it services|software solutions|talent|recruit|global solutions|"
    r"technologies llc|tek systems|teksystems|randstad|kforce|apex systems|robert half|vista applied|inrika|collabera|"
    r"diverse lynx|mastech|igen|sysintelli|ampcus|xoriant|saxon|softpath|amiti|e-solutions|ebusiness|enterprise solutions|"
    r"brillio|egen solutions|apex technology|bridge group|genesis corp|ispace inc|solutions llc$|systems llc$|consulting llc$|"
    r"global llc$|tech llc$|innovations llc$|inc dba|\bit\b",
    re.I,
)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)


def _rows(title: str, year: int) -> list[list[str]]:
    import html as h
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{title.replace(' ', '_')}_{year}.json"
    if path.exists() and time.time() - path.stat().st_mtime < 7 * 24 * 3600:
        return json.loads(path.read_text())
    time.sleep(1.0)
    url = f"https://h1bdata.info/index.php?em=&job={urllib.parse.quote(title)}&city=&year={year}"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=120)
    resp.raise_for_status()
    rows = [[h.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in _CELL.findall(r)][:6] for r in _ROW.findall(resp.text)[1:]]
    rows = [r for r in rows if len(r) >= 4]
    path.write_text(json.dumps(rows))
    return rows


def _slug_index() -> dict[str, list[tuple[str, str]]]:
    """normalized slug -> [(ats, slug)] from the public board lists."""
    idx: dict[str, list[tuple[str, str]]] = {}
    for ats in ("greenhouse", "lever", "ashby"):
        for slug in json.loads((RAW / f"{ats}_companies.json").read_text()):
            key = re.sub(r"[^a-z0-9]", "", slug.lower())
            key = re.sub(r"(jobs|careers|hq|inc|usa|us|\d+)$", "", key)
            idx.setdefault(key, []).append((ats, slug))
    return idx


def _greenhouse_name(slug: str) -> str | None:
    time.sleep(1.0)
    try:
        r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}", headers={"User-Agent": USER_AGENT}, timeout=20)
        return r.json().get("name") if r.ok else None
    except (requests.RequestException, ValueError):
        return None


def _page_title_matches(ats: str, slug: str, key: str) -> bool:
    """Lever and Ashby APIs don't return the company name, so check the public board page's <title>."""
    url = {"lever": f"https://jobs.lever.co/{slug}", "ashby": f"https://jobs.ashbyhq.com/{slug}"}[ats]
    time.sleep(1.0)
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    except requests.RequestException:
        return False
    m = re.search(r"<title>(.*?)</title>", r.text or "", re.S | re.I)
    title = norm(m.group(1)) if m else ""
    return bool(title) and (key in title or title.startswith(key[:8]))


def run(min_filings: int = 5, known: set[str] | None = None) -> list[dict]:
    known = {norm(k) for k in (known or set())}
    employers: dict[str, dict] = {}
    for title in TITLES:
        for year in YEARS:
            for employer, job_title, salary, location, *_ in _rows(title, year):
                if STAFFING.search(employer):
                    continue
                role = role_for_title(job_title)
                if role is None:
                    continue
                e = employers.setdefault(norm(employer), {"name": employer, "filings": 0, "by_role": {}, "boston": 0})
                e["filings"] += 1
                e["by_role"][role.key] = e["by_role"].get(role.key, 0) + 1
                if re.search(r",\s*MA$", location):
                    e["boston"] += 1
    idx = _slug_index()
    found = []
    for key, e in sorted(employers.items(), key=lambda kv: -kv[1]["filings"]):
        if e["filings"] < min_filings or key in known or len(key) < 4:
            continue
        for ats, slug in idx.get(key, []):
            if ats == "greenhouse":
                board = _greenhouse_name(slug)
                if not board or norm(board) != key and not norm(board).startswith(key):
                    continue
            elif not _page_title_matches(ats, slug, key):
                continue
            found.append({"name": e["name"].title(), "ats": ats, "slug": slug, "filings": e["filings"],
                          "by_role": e["by_role"], "boston": e["boston"]})
            break
    OUT.write_text(yaml.safe_dump(found, sort_keys=False))
    return found
