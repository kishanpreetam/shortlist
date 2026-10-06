"""Public job-board APIs: Greenhouse, Lever and Ashby.

All three publish postings as public, no-login JSON. We fetch at most one
request per second per host, identify ourselves in the User-Agent, and keep
nothing but the fields the scorer needs.
"""

from __future__ import annotations

import html
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

USER_AGENT = "shortlist/0.1 (personal job search tool)"
TIMEOUT_S = 30
MIN_INTERVAL_S = 1.0

_last: dict[str, float] = {}
_lock = threading.Lock()


@dataclass
class Posting:
    company: str
    source: str  # greenhouse | lever | ashby
    title: str
    location: str
    url: str
    text: str
    posted_at: datetime | None = None
    remote: bool | None = None
    salary_min: float | None = None  # annual USD when known
    salary_max: float | None = None
    employment_type: str = ""
    departments: list[str] = field(default_factory=list)


def _get_json(url: str):
    host = url.split("/")[2]
    with _lock:
        wait = _last.get(host, 0) + MIN_INTERVAL_S - time.monotonic()
        _last[host] = time.monotonic() + max(wait, 0)
    if wait > 0:
        time.sleep(wait)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=TIMEOUT_S)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _text(raw_html: str) -> str:
    s = html.unescape(html.unescape(raw_html or ""))  # Greenhouse double-escapes content
    s = re.sub(r"<(br|/p|/li|/h\d|/div)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def _dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):  # epoch ms
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _annual(amount: float | None, interval: str | None) -> float | None:
    if amount is None:
        return None
    i = (interval or "").lower()
    if "hour" in i:
        return amount * 2080
    if "month" in i:
        return amount * 12
    if "week" in i:
        return amount * 52
    return amount


def greenhouse(token: str, company: str) -> list[Posting]:
    data = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    out = []
    for j in (data or {}).get("jobs", []):
        text = _text(j.get("content", ""))
        lo = hi = None
        m = re.search(r"\$\s?([\d,]{5,9})(?:\.\d+)?\s*(?:-|–|—|to)\s*\$?\s?([\d,]{5,9})", text)
        if m:  # pay transparency text inside the description
            lo, hi = (float(x.replace(",", "")) for x in m.groups())
        out.append(Posting(
            company=company, source="greenhouse", title=j.get("title", ""),
            location=(j.get("location") or {}).get("name", ""), url=j.get("absolute_url", ""), text=text,
            posted_at=_dt(j.get("first_published") or j.get("updated_at")),
            salary_min=lo, salary_max=hi,
            departments=[d.get("name", "") for d in j.get("departments", [])],
        ))
    return out


def lever(site: str, company: str) -> list[Posting]:
    data = _get_json(f"https://api.lever.co/v0/postings/{site}?mode=json")
    out = []
    for j in data or []:
        cats = j.get("categories") or {}
        sal = j.get("salaryRange") or {}
        parts = [j.get("descriptionPlain", "")] + [
            f"{lst.get('text', '')}\n{_text(lst.get('content', ''))}" for lst in j.get("lists", [])
        ] + [j.get("additionalPlain", "")]
        out.append(Posting(
            company=company, source="lever", title=j.get("text", ""),
            location=cats.get("location", "") or ", ".join(cats.get("allLocations", []) or []),
            url=j.get("hostedUrl", ""), text="\n".join(p for p in parts if p),
            posted_at=_dt(j.get("createdAt")),
            remote=(j.get("workplaceType") == "remote") if j.get("workplaceType") not in (None, "unspecified") else None,
            salary_min=_annual(sal.get("min"), sal.get("interval")) if sal.get("currency", "USD") == "USD" else None,
            salary_max=_annual(sal.get("max"), sal.get("interval")) if sal.get("currency", "USD") == "USD" else None,
            employment_type=cats.get("commitment", "") or "",
            departments=[x for x in (cats.get("team"), cats.get("department")) if x],
        ))
    return out


def ashby(org: str, company: str) -> list[Posting]:
    data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true")
    out = []
    for j in (data or {}).get("jobs", []):
        lo = hi = None
        for comp in ((j.get("compensation") or {}).get("summaryComponents") or []):
            if comp.get("compensationType") == "Salary" and comp.get("currencyCode", "USD") == "USD":
                lo = _annual(comp.get("minValue"), comp.get("interval"))
                hi = _annual(comp.get("maxValue"), comp.get("interval"))
        loc = j.get("location", "") or ""
        extra = [s.get("location", "") for s in (j.get("secondaryLocations") or []) if isinstance(s, dict)]
        out.append(Posting(
            company=company, source="ashby", title=j.get("title", ""),
            location="; ".join([loc] + extra) if extra else loc, url=j.get("jobUrl", ""),
            text=j.get("descriptionPlain", "") or _text(j.get("descriptionHtml", "")),
            posted_at=_dt(j.get("publishedAt")), remote=j.get("isRemote"),
            salary_min=lo, salary_max=hi, employment_type=j.get("employmentType", "") or "",
            departments=[x for x in (j.get("department"), j.get("team")) if x],
        ))
    return out


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}
