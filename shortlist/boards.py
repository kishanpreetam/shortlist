"""Public job-board APIs: Greenhouse, Lever, Ashby, Workday and Workable.

All five publish postings as public, no-login JSON. We fetch at most one
request per second per host, identify ourselves in the User-Agent, follow each
host's robots.txt, and keep nothing but the fields the scorer needs.
(SmartRecruiters is left out on purpose: its API's robots.txt disallows crawlers.)
"""

from __future__ import annotations

import html
import re
import threading
import time
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

from . import classify
from .roles import role_for_title

USER_AGENT = "shortlist/0.1 (personal job search tool)"
TIMEOUT_S = 30
MIN_INTERVAL_S = 1.0

_last: dict[str, float] = {}
_lock = threading.Lock()


@dataclass
class Posting:
    company: str
    source: str  # greenhouse | lever | ashby | workday | workable
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


def _wait_turn(host: str) -> None:
    """Spaces requests to one host at least MIN_INTERVAL_S apart, across threads."""
    with _lock:
        wait = _last.get(host, 0) + MIN_INTERVAL_S - time.monotonic()
        _last[host] = time.monotonic() + max(wait, 0)
    if wait > 0:
        time.sleep(wait)


def _get_json(url: str, payload: dict | None = None):
    """GET the URL, or POST it when a JSON payload is given. None on 404.

    A rate limit (429) or a server error gets one retry after the server's
    Retry-After (10 seconds if it gives none, at most a minute)."""
    host = url.split("/")[2]
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    for attempt in range(2):
        _wait_turn(host)
        if payload is None:
            resp = requests.get(url, headers=headers, timeout=TIMEOUT_S)
        else:
            resp = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT_S)
        if resp.status_code not in (429, 500, 502, 503, 504) or attempt:
            break
        try:
            time.sleep(min(float(resp.headers.get("Retry-After", 10)), 60))
        except ValueError:  # an HTTP date instead of seconds
            time.sleep(10)
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
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # date-only values, e.g. Workday's startDate


_PAY_RANGE = re.compile(r"\$\s?([\d,]{5,9})(?:\.\d+)?\s*(?:-|–|—|to)\s*\$?\s?([\d,]{5,9})")


def _salary_range(text: str) -> tuple[float | None, float | None]:
    """A pay-transparency range such as "$120,000 - $150,000" inside a description."""
    m = _PAY_RANGE.search(text)
    if not m:
        return None, None
    lo, hi = (float(x.replace(",", "")) for x in m.groups())
    return lo, hi


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
        lo, hi = _salary_range(text)
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
            location=cats.get("location", "") or "; ".join(cats.get("allLocations", []) or []),
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


# --- Workday ---------------------------------------------------------------------
# A career site at https://{tenant}.{wdN}.myworkdayjobs.com/{site} loads its jobs from a
# public JSON API under /wday/cxs/. Every tenant is its own host, so the one-request-a-
# second limit applies per employer, and a few tenants are read at once.
#
# The API's search is full text and returns best matches or newest jobs first. Rather
# than paging through every job, we run a few searches for the role families and end
# each one once it stops turning up new jobs or titles worth scoring (descriptions
# share boilerplate, so broad words often match the same jobs). Details are fetched
# only for titles in a role family that the scorer could keep.

WORKDAY_SEARCHES = ("data", "machine learning", "AI", "analytics", "scientist", "analyst", "software")
WORKDAY_PAGE = 20  # the API's largest page
WORKDAY_MAX_PAGES = 10  # per search
WORKDAY_AT_ONCE = 6  # tenants read in parallel
WORKDAY_US = "bc33aa3152ec42d4995f4791a106ed09"  # Workday's id for the United States, the same in every tenant
_workday_slots = threading.BoundedSemaphore(WORKDAY_AT_ONCE)


class RobotsDisallowed(Exception):
    """The tenant's robots.txt asks crawlers to stay out of its job API or its job pages."""


def host_for(ats: str, slug: str) -> str:
    """The host a board's requests go to. Boards on different hosts can be read in parallel."""
    if ats == "workday":
        tenant, cluster, _ = slug.split("/")
        return f"{tenant}.{cluster}.myworkdayjobs.com"
    return ats  # Greenhouse, Lever and Ashby each serve all their boards from one API host


def _robots_allow(host: str, paths: list[str]) -> bool:
    """False when the host's robots.txt disallows any of the paths for us. Workday treats
    site names case-insensitively, so the rules are matched that way too."""
    _wait_turn(host)
    resp = requests.get(f"https://{host}/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S)
    if resp.status_code in (401, 403):
        return False
    if resp.status_code >= 500:
        resp.raise_for_status()
    if resp.status_code != 200:  # no robots.txt, so nothing is disallowed
        return True
    rules = urllib.robotparser.RobotFileParser()
    rules.parse(resp.text.lower().splitlines())
    return all(rules.can_fetch(USER_AGENT, f"https://{host}{p.lower()}") for p in paths)


def _posted_on(text: str | None) -> datetime | None:
    """Workday's relative dates: "Posted Today", "Posted Yesterday", "Posted 30+ Days Ago"."""
    s = (text or "").lower()
    if "today" in s:
        days = 0
    elif "yesterday" in s:
        days = 1
    elif m := re.search(r"(\d+)(\+?)\s*days?", s):
        days = int(m.group(1)) + (1 if m.group(2) else 0)  # "30+" means older than 30
    else:
        return None
    return datetime.now(timezone.utc) - timedelta(days=days)


def _us_only(facets: list | None) -> dict:
    """appliedFacets that keep only US jobs, when the tenant offers a country filter."""
    stack = list(facets or [])
    while stack:
        facet = stack.pop()
        for value in facet.get("values") or []:
            if "facetParameter" in value:  # a nested group of filters
                stack.append(value)
            elif value.get("id") == WORKDAY_US and facet.get("facetParameter"):
                return {facet["facetParameter"]: [WORKDAY_US]}
    return {}


_STATE_NAMES = ("alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii idaho "
                "illinois indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota "
                "mississippi missouri montana nebraska nevada new_hampshire new_jersey new_mexico new_york north_carolina "
                "north_dakota ohio oklahoma oregon pennsylvania rhode_island south_carolina south_dakota tennessee texas "
                "utah vermont virginia washington west_virginia wisconsin wyoming district_of_columbia")
_STATE_CODE = dict(zip((n.replace("_", " ") for n in _STATE_NAMES.split()),
                       "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC "
                       "ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()))
_COUNTRY = r"(?:united states(?: of america)?|usa?|amer)"
_PLACE_FORMATS = [re.compile(rx) for rx in (
    r"^(?P<city>[A-Za-z .'-]+?)\s*,\s*(?P<state>[A-Za-z .]+)$",  # Spring, Texas / Denver, CO
    r"^(?P<state>[A-Z]{2})\s*-\s*(?P<city>[A-Za-z .']+?)(?:\s*[,(].*)?$",  # IL-CHICAGO, 233 S Wacker / PA - Pittsburgh (15222)
    r"^(?P<city>[A-Za-z .']+?)(?:-|\s+)(?P<state>[A-Z]{2})(?:\s+-\s+.*)?$",  # Boston-MA / Carmel IN
    r"^(?P<state>[A-Za-z ]+?)\s*[,-]\s*(?P<city>[A-Za-z .']+?)(?:\s+-\s+.*)?$",  # Colorado - Denver - Wewatta / CA, Santa Clara
)]


def _city_state(place: str) -> str:
    """A Workday place as "City, ST" when it names a US city in one of Workday's usual
    formats, so the wage tables can find its area. Anything else comes back unchanged."""
    if re.search(r"\bremote\b", place, re.I):
        return place
    s = re.sub(rf"^(?:{_COUNTRY}\s*[,-]\s*)+|(?:\s*[,-]\s*{_COUNTRY})+$", "", place.strip(), flags=re.I)
    for rx in _PLACE_FORMATS:
        m = rx.match(s)
        if not m or re.fullmatch(_COUNTRY, m["city"].strip(), re.I):
            continue
        state, city = m["state"].strip(), m["city"].strip()
        code = state.upper() if state.upper() in classify.US_STATES else _STATE_CODE.get(state.lower())
        if code:
            return f"{city.title() if city.isupper() else city}, {code}"
    return place


_TITLE_RULED_OUT = ("senior", "internship", "postdoc", "off_profile")  # seniorities the scorer always excludes


def _could_fit(title: str) -> bool:
    return role_for_title(title) is not None and classify.seniority(title) not in _TITLE_RULED_OUT


def _workday_search(api: str) -> dict[str, dict]:
    """{externalPath: search result} for every job the role-family searches find.

    A search ends when a page brings no new jobs, or when two pages in a row bring no
    title the scorer could keep."""
    found: dict[str, dict] = {}
    facets: dict | None = None
    for query in WORKDAY_SEARCHES:
        total, dry = 0, 0
        for page in range(WORKDAY_MAX_PAGES):
            body = {"appliedFacets": facets or {}, "limit": WORKDAY_PAGE, "offset": page * WORKDAY_PAGE, "searchText": query}
            data = _get_json(f"{api}/jobs", body) or {}
            if facets is None:  # the first response shows whether the tenant can filter by country
                facets = _us_only(data.get("facets"))
                if facets:
                    data = _get_json(f"{api}/jobs", body | {"appliedFacets": facets}) or {}
            total = data.get("total") or total  # only the first page carries the total
            jobs = data.get("jobPostings") or []
            new = [j for j in jobs if j.get("externalPath") and j["externalPath"] not in found]
            found.update((j["externalPath"], j) for j in new)
            dry = 0 if any(_could_fit(j.get("title", "")) for j in new) else dry + 1
            if not new or dry >= 2 or len(jobs) < WORKDAY_PAGE or (page + 1) * WORKDAY_PAGE >= total:
                break
    return found


def _ruled_out(p: Posting) -> bool:
    """True when the scorer will drop the posting on its title or place alone, so its
    details wouldn't change anything."""
    if classify.seniority(p.title) in _TITLE_RULED_OUT:
        return True
    return bool(classify.NON_US.search(p.location)) and not classify.is_us(p.location, p.title)


def _workday_detail(info: dict, listed: Posting, job: dict) -> Posting:
    text = _text(info.get("jobDescription", ""))
    lo, hi = _salary_range(text)
    places = [info.get("location")] + list(info.get("additionalLocations") or [])
    location = "; ".join(_city_state(p) for p in places if p) or listed.location
    if (info.get("country") or {}).get("descriptor") == "United States of America" and not classify.is_us(location):
        location = f"{location}, United States" if location else "United States"  # e.g. "Brockton Behavioral Health Center"
    kind = (info.get("remoteType") or job.get("remoteType") or "").lower()
    return Posting(
        company=listed.company, source="workday", title=info.get("title") or listed.title,
        location=location, url=info.get("externalUrl") or listed.url, text=text,
        posted_at=_dt(info.get("startDate")) or listed.posted_at,
        remote=("remote" in kind and "hybrid" not in kind) if kind else None,
        salary_min=lo, salary_max=hi, employment_type=info.get("timeType", "") or "",
    )


def workday(slug: str, company: str) -> list[Posting]:
    """slug is "tenant/wdN/site", as in the career site's address: "northeastern/wd1/careers"."""
    tenant, _, site = slug.split("/")
    host = host_for("workday", slug)
    api = f"https://{host}/wday/cxs/{tenant}/{site}"
    with _workday_slots:
        if not _robots_allow(host, [f"/wday/cxs/{tenant}/{site}/jobs", f"/{site}/"]):
            raise RobotsDisallowed(f"{host} disallows /wday/cxs/ or /{site}/")
        out, failures = [], 0
        for path, job in _workday_search(api).items():
            title = job.get("title", "")
            if role_for_title(title) is None:
                continue
            listed = Posting(company=company, source="workday", title=title, location=_city_state(job.get("locationsText", "")),
                             url=f"https://{host}/{site}{path}", text="", posted_at=_posted_on(job.get("postedOn")))
            if _ruled_out(listed):
                out.append(listed)
                continue
            try:
                data = _get_json(f"{api}{path}")
            except requests.RequestException:
                failures += 1
                if failures > 3:  # not one bad posting but a broken or blocking tenant
                    raise
                continue
            info = (data or {}).get("jobPostingInfo")
            if info:  # None when the job came down after the search
                out.append(_workday_detail(info, listed, job))
    return out


# --- Workable --------------------------------------------------------------------
# apply.workable.com/{account} serves its jobs, with descriptions, from the public widget API.
_WORKABLE_US = {"united states", "united states of america", "usa", "us"}
_workable_robots: dict[str, bool] = {}


def workable(account: str, company: str) -> list[Posting]:
    host = "apply.workable.com"
    if host not in _workable_robots:
        _workable_robots[host] = _robots_allow(host, ["/api/v1/widget/accounts/"])
    if not _workable_robots[host]:
        raise RobotsDisallowed(f"{host} disallows /api/v1/widget/accounts/")
    data = _get_json(f"https://{host}/api/v1/widget/accounts/{account}?details=true") or {}
    out = []
    for j in data.get("jobs") or []:
        country = (j.get("country") or "").strip()
        codes = {(loc.get("countryCode") or "").upper() for loc in j.get("locations") or [] if isinstance(loc, dict)}
        us = country.lower() in _WORKABLE_US or "US" in codes
        place = ", ".join(x for x in (j.get("city"), j.get("state")) if x)
        location = _city_state(f"{place}, United States") if us else ", ".join(x for x in (place, country) if x)
        remote = str(j.get("telecommuting")).lower() == "true"
        if remote and us and not place:
            location = "Remote, United States"
        text = _text(j.get("description", ""))
        lo, hi = _salary_range(text)
        out.append(Posting(
            company=company, source="workable", title=j.get("title", ""), location=location,
            url=j.get("url") or j.get("shortlink") or f"https://{host}/{account}/j/{j.get('shortcode', '')}", text=text,
            posted_at=_dt(j.get("published_on") or j.get("created_at")), remote=remote,
            salary_min=lo, salary_max=hi, employment_type=j.get("employment_type") or "",
            departments=[j["department"]] if j.get("department") else [],
        ))
    return out


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workday": workday, "workable": workable}
