"""A daily apply queue, an application tracker, and a local page to work through both.

    python -m shortlist today              rescan if results are over 6 hours old, pick today's
                                           queue, and open it at http://127.0.0.1:8765
    python -m shortlist applied URL|N      log an application (a URL, or a number from today's queue)
    python -m shortlist update URL --outcome screen
    python -m shortlist followups          follow-ups due today, with the message to send

Picking: realistic postings only, startups first and then established employers
(`is_startup`), each ranked by score times a freshness weight. Most
applications to a posting arrive in its first 2 to 4 days and reviewers stop once the
shortlist is strong, so a posting from the last 2 days counts 2x and one from last
month 0.6x. At most `cap` applications per employer in any 60 days (interview rates
fall with each extra application to the same company), one per job title, no titles
matching `exclude_titles`, and nothing you've applied to or skipped. The top
`messages` jobs get a note for one person there; LinkedIn allows about 100 invitations
a week, so the default is 20 a day.

Applications live in results/applications.csv, which is gitignored like the rest of
results/. The page listens on 127.0.0.1 only, and every write needs a per-run token.

Resumes are tailored separately, by Tailor (`python -m tailor`, its own page on port 8766, or `tailor_port` in
profile.yaml). Once `resume_facts` is set, each card links to it with that job, and nothing here imports it.
"""

from __future__ import annotations

import csv
import json
import re
import secrets
import subprocess
import sys
import threading
import webbrowser
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

from pathlib import Path


from . import h1bdata, linkedin, outcomes, startups
from .claude import ClaudeError, claude_path
from .classify import SKILL_LABELS
from .paths import DATA, RESULTS
from .roles import role_for_title

POSTINGS = RESULTS / "postings.csv"
APPS = RESULTS / "applications.csv"
SKIPS = RESULTS / "skipped.csv"
TODAY = RESULTS / "today.json"
APP_FIELDS = ["applied_on", "company", "title", "role", "url", "channel", "contact", "status", "status_on",
              "follow_up_1", "follow_up_1_done", "follow_up_2", "follow_up_2_done", "notes"]
SKIP_FIELDS = ["skipped_on", "company", "title", "url", "reason"]
SKIP_REASONS = ("no sponsorship", "too senior", "not a fit", "closed", "other")
BANDS = (3, 14, 30, 60)            # days since posting, for display
FRESH_WEIGHT = ((2, 2.0), (4, 1.6), (7, 1.3), (14, 0.9), (30, 0.6), (60, 0.4))  # (max days old, weight)
FOLLOW_UP_BUSINESS_DAYS = (6,)  # first interviews typically come about 6 days after applying; one note, then stop
# Titles the role classifier lets through that are rarely a data or AI engineering job.
# Add your own with `exclude_titles: ["regex", ...]` in profile.yaml.
EXCLUDE_TITLES = (r"\bIT (systems|support|operations)\b", r"client platform", r"help ?desk", r"developer (relations|advocate)",
                  r"\b(customer|developer) success\b", r"\bsupport engineer\b", r"\bsales\b", r"account (executive|manager)",
                  r"\brecruit", r"procure", r"payroll", r"\bGTM\b",
                  r"\bPhD\b", r"\b(spanish|french|german|japanese|korean|mandarin|portuguese)\b",
                  r"\bresearch associate\b",  # at hospitals and labs this is usually a clinical or bench role
                  # senior levels the title classifier misses: "Leader, ...", "Principle" (sic), "Advisor"
                  # at insurers, and numbered levels such as "Data Engineer 5"
                  r"\bleader\b", r"\bprinciple\b", r"\badvisor\b", r"\b(LMTS|SMTS|PMTS)\b", r"\bresearcher\s+[3-9]\b",
                  r"\bU\.?S\.? government\b",  # these roles need citizenship or a clearance
                  r"2027\b.*\bgrad|\bgrad\w*\b.*\b2027", r"\baccounting\b",  # 2027-graduate programs; accounting jobs
                  # lab science and business development, which share words with data roles
                  r"\b(pharmacology|toxicology|chemist(ry)?|assay|in vivo|wet lab)\b", r"\bBD\b", r"business development",
                  r"\b(engineer|scientist|analyst|developer)\s+[3-9]\b")
STATUSES = ("applied", "screen", "interview", "offer", "rejected", "no_response")

# Which resume version fits each role family. Override with `resume_for` in profile.yaml.
RESUME_FOR = {
    "ai_engineer": "ai-engineer", "forward_deployed": "ai-engineer", "consulting": "ai-engineer",
    "software_engineer": "ai-engineer",
    "data_engineer": "data-engineer", "analytics_engineer": "data-engineer", "bi_engineer": "data-engineer",
    "data_scientist": "data-scientist", "ml_engineer": "data-scientist", "research_engineer": "data-scientist",
    "data_analyst": "data-analyst", "business_analyst": "data-analyst",
}
MANAGER_WORDS = {
    "data-engineer": '"data engineering manager" OR "head of data" OR "data platform"',
    "ai-engineer": '"engineering manager" OR "head of AI" OR "applied AI"',
    "data-scientist": '"data science manager" OR "head of data science" OR "machine learning manager"',
    "data-analyst": '"analytics manager" OR "head of analytics" OR "BI manager"',
}
# New-grad programs often require a graduation date in a set window (for "2027" programs,
# usually Dec 2026 to mid-2027), so the card asks you to check it first.
GRAD_PROGRAM = re.compile(r"new grad|university grad|graduate program|early career program|\b20(26|27)\b", re.I)
_lock = threading.Lock()


# ---------- small helpers ----------

def _read(path, fields=None) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    if fields:
        rows = [{k: r.get(k, "") or "" for k in fields} for r in rows]
    return rows


def _write(path, fields, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def _d(s: str) -> date:
    try:
        return date.fromisoformat(s[:10])
    except (TypeError, ValueError):
        return date.min


def add_business_days(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def title_key(title: str) -> str:
    """'Software Engineer, Early Career (NYC)' and '... (SF)' are one job title."""
    t = re.sub(r"\(.*?\)", " ", title.lower())
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def clean_title(title: str) -> str:
    return " ".join(title.split()).strip(" -,")


def band(age: int) -> int:
    for i, limit in enumerate(BANDS):
        if age <= limit:
            return i
    return len(BANDS)


def fresh_weight(age: int) -> float:
    for limit, w in FRESH_WEIGHT:
        if age <= limit:
            return w
    return 0.0


def board_of(url: str) -> str:
    u = url.lower()
    # Workday hosts some tenants' job pages on myworkdaysite.com (wd5.myworkdaysite.com/recruiting/guidewire/...)
    for name, marks in (("Workday", ("myworkdayjobs.com", "myworkdaysite.com")), ("Ashby", ("ashbyhq.com",)), ("Lever", ("lever.co",)),
                        ("Greenhouse", ("greenhouse.io", "gh_jid="))):
        if any(m in u for m in marks):
            return name
    return "Other"


_POSTING_INDEX: dict[str, str] | None = None


def posting_text(url: str) -> str:
    """The full job description Shortlist fetched for this posting (from the cached job boards)."""
    global _POSTING_INDEX
    if _POSTING_INDEX is None:
        index = {}
        for f in (DATA / "boards").glob("*.json"):
            try:
                for j in json.loads(f.read_text()):
                    index.setdefault(j.get("url", ""), str(f))
            except (OSError, ValueError):
                continue
        _POSTING_INDEX = index
    f = _POSTING_INDEX.get(url)
    if not f:
        return ""
    try:
        return next((j.get("text") or "" for j in json.loads(Path(f).read_text()) if j.get("url") == url), "")
    except (OSError, ValueError):
        return ""


REFRESH_TTL_S = 15 * 60  # Refresh skips a board read in the last 15 minutes
REFRESH_HOURS = (24, 48)  # the window Refresh keeps: you pick from 24 to 48 hours, never more
REFRESH: dict = {"running": False, "done": 0, "total": 0, "stage": "", "error": "", "finished": "", "found": None, "hours": 24}
_REFRESH_LOCK = threading.Lock()


def refresh_hours(value) -> int:
    """The Refresh window you picked: a whole number of hours from 24 to 48."""
    try:
        hours = float(value)
    except (TypeError, ValueError):
        hours = float("nan")
    if not (REFRESH_HOURS[0] <= hours <= REFRESH_HOURS[1] and hours == int(hours)):
        raise ValueError(f"Pick {REFRESH_HOURS[0]} to {REFRESH_HOURS[1]} hours.")
    return int(hours)


def start_refresh(profile: dict, hours=24) -> dict:
    """The page's Refresh button: read every employer's job board again and pick today's queue from postings of the
    last `hours` (24 to 48), in the background (about 10 minutes), while the page shows how far it got. One at a time."""
    hours = refresh_hours(hours)
    with _REFRESH_LOCK:
        if not REFRESH["running"]:
            REFRESH.update(running=True, done=0, total=0, stage="Looking up sponsors", error="", found=None, hours=hours)
            threading.Thread(target=_refresh, args=(profile, hours), daemon=True).start()
        return dict(REFRESH)


def _refresh(profile: dict, hours: int = 24) -> None:
    global _POSTING_INDEX
    from .__main__ import scan  # the same scan as `python -m shortlist run`
    try:
        scan(profile, ttl_s=REFRESH_TTL_S,
             progress=lambda done, total: REFRESH.update(done=done, total=total, stage="Reading job boards"))
        REFRESH["stage"] = "Picking today's jobs"
        old = json.loads(TODAY.read_text()) if TODAY.exists() else {}
        queue = make_queue(profile, n=int(old.get("target") or 30), max_age_hours=hours)
        _POSTING_INDEX = None  # the new postings' descriptions are in the boards just read
        REFRESH["found"] = len([i for i in queue["items"] if not i["spare"]])
    except Exception as exc:  # the page says what went wrong, and the queue you have stays
        REFRESH["error"] = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        REFRESH.update(running=False, finished=datetime.now().isoformat(timespec="minutes"))


def linkedin_check(d: dict, profile: dict) -> dict:
    """Jobs from a LinkedIn page you copied, each with its company's H-1B filings, the posting on the employer's own
    board when Shortlist reads it (and its place in today's queue), and whether you applied. Companies that file
    for H-1B in your roles come first."""
    from .__main__ import load_companies, sponsor_records
    from .score import Company
    text = str(d.get("text") or "")[:120_000]
    if len(text.strip()) < 40:
        raise ValueError("Paste the whole LinkedIn page: open a search, press Cmd+A, then Cmd+C, and paste here.")
    claude = claude_path(profile)
    if not claude:
        raise ValueError("Reading a LinkedIn page needs Claude Code installed and signed in (claude auth login).")
    try:
        jobs = linkedin.read_page(text, linkedin.clean_links(d.get("links")), claude)
    except ClaudeError as exc:
        raise ValueError(str(exc)) from exc
    if not jobs:
        return {"jobs": [], "note": "No jobs found in that copy. Click inside the results list, press Cmd+A, then Cmd+C, and paste again."}
    known = {h1bdata.norm(c.name): c for c in load_companies(profile)}

    def company_for(name: str):  # Shortlist's entry for this employer (its legal name for H-1B), if it has one
        key = h1bdata.norm(name)
        return known.get(key) or next((c for k, c in known.items() if len(k) >= 4 and len(key) >= 4
                                       and (k.startswith(key) or key.startswith(k))), None)

    def sponsor(name: str):
        c = company_for(name) or Company(name=name, boards=[])
        try:
            return name, sponsor_records([c])[0].get(c.name)
        except Exception:  # an H-1B lookup that fails leaves the job unchecked, not the page broken
            return name, None
    with ThreadPoolExecutor(8) as pool:
        sponsors = dict(pool.map(sponsor, sorted({j["company"] for j in jobs})))
    by_co: dict[str, list[dict]] = {}
    for r in _read(POSTINGS):
        by_co.setdefault(h1bdata.norm(r["company"]), []).append(r)
    queue = json.loads(TODAY.read_text()) if TODAY.exists() else {"items": []}
    in_queue = {i["url"]: i["n"] for i in queue.get("items", [])}
    apps = _read(APPS, APP_FIELDS)
    applied = {a["url"] for a in apps} | {f"{h1bdata.norm(a['company'])}|{title_key(a['title'])}" for a in apps}
    mine = set(profile.get("roles") or [])
    for j in jobs:
        c, t = company_for(j["company"]), title_key(j["title"])
        rows = by_co.get(h1bdata.norm(c.name if c else j["company"])) or []
        same = [r for r in rows if title_key(r["title"]) == t] or \
            [r for r in rows if t and (t in title_key(r["title"]) or title_key(r["title"]) in t)]
        best = max(same, key=lambda r: float(r.get("score") or 0), default=None)
        j["own"] = {"url": best["url"], "title": best["title"], "n": in_queue.get(best["url"]),
                    "skipped": best.get("excluded") or ""} if best and best["url"].startswith("https://") else None
        rec = sponsors.get(j["company"])
        j["sponsor"] = {"filings": int(rec.get("filings") or 0), "yours": sum((rec.get("by_role") or {}).get(k, 0) for k in mine),
                        "source": rec.get("source", "")} if rec else None
        role = role_for_title(j["title"])
        j["role"] = role.key if role else ""
        j["track_url"] = j["link"] or (j["own"] or {}).get("url") or \
            f"https://www.linkedin.com/jobs/search/?keywords={quote(j['company'] + ' ' + j['title'])}"
        j["applied"] = j["track_url"] in applied or f"{h1bdata.norm(j['company'])}|{t}" in applied
        j["find"] = f"https://www.google.com/search?q={quote(j['company'] + ' ' + j['title'] + ' careers')}"
    jobs.sort(key=lambda j: (-(j["sponsor"] or {}).get("yours", 0), -(j["sponsor"] or {}).get("filings", 0)))
    return {"jobs": jobs, "note": ""}


def resume_variant(role: str, profile: dict) -> str:
    return (profile.get("resume_for") or {}).get(role) or RESUME_FOR.get(role, "ai-engineer")


def _mention(leverage: str) -> str:
    """'FirstCall is direct proof, Scrapitch is direct proof' -> 'Mention FirstCall and Scrapitch'."""
    names = [x.replace(" is direct proof", "").strip() for x in leverage.split(",") if "direct proof" in x]
    other = [x.strip() for x in leverage.split(",") if x.strip() and "direct proof" not in x]
    parts = ([f"Mention {' and '.join(names)}"] if names else []) + other
    return "; ".join(parts)


def labels(keys: str) -> list[str]:
    return [SKILL_LABELS.get(k, k.replace("_", " ")) for k in keys.split() if k]


# ---------- picking today's queue ----------

def date_only(r: dict) -> bool:
    """True when the board gave a date but no time (Workday), stored as midnight UTC."""
    return (r.get("posted_at") or "").endswith("T00:00:00+00:00")


def hours_old(r: dict, age_offset: int = 0, now: datetime | None = None) -> float:
    """Hours since a posting was published: from its publish time when the board gave one, else from its age in days.
    A date with no time counts from noon US Eastern that day (16:00 UTC), the middle of a US posting day."""
    when = r.get("posted_at") or ""
    if when:
        try:
            t = datetime.fromisoformat(when)
            if date_only(r):
                t = t.replace(hour=16)
            now = now or (datetime.now(t.tzinfo) if t.tzinfo else datetime.now())
            return max(0.0, (now - t).total_seconds() / 3600)
        except ValueError:
            pass
    days = int(r["age_days"]) if (r.get("age_days") or "").lstrip("-").isdigit() else 30
    return 24.0 * (days + age_offset)


def pick(rows, apps, skips, n=30, cap=2, max_age=60, min_score=3.0, today=None,
         age_offset=0, exclude_titles=EXCLUDE_TITLES, max_age_hours: float | None = None) -> list[dict]:
    """age_offset: whole days between the scan and now, added to each posting's age. max_age_hours: keep only
    postings published that recently (Workday says only "today" or "yesterday", so its "yesterday" counts as 24-48 hours)."""
    today = today or date.today()
    recent = today - timedelta(days=60)
    per_company = Counter(a["company"] for a in apps if _d(a["applied_on"]) >= recent)
    done = {a["url"] for a in apps} | {s["url"] for s in skips}
    titles = {(a["company"], title_key(a["title"])) for a in apps}
    blocked = re.compile("|".join(f"(?:{p})" for p in exclude_titles), re.I) if exclude_titles else None
    candidates = []
    for r in rows:
        score = float(r.get("score") or 0)
        if r.get("excluded") or score <= 0 or score < min_score or r["url"] in done:
            continue
        if blocked and blocked.search(r["title"]):
            continue
        age = (int(r["age_days"]) if (r.get("age_days") or "").lstrip("-").isdigit() else 30) + age_offset
        hours = hours_old(r, age_offset)
        if age > max_age or (max_age_hours is not None and hours > max_age_hours):
            continue
        first = 0 if is_startup(r["company"], board_of(r["url"])) else 1  # startups first, then established employers
        candidates.append((first, -score * fresh_weight(age), age, r | {"age_hours": None if date_only(r) else round(hours)}))
    candidates.sort(key=lambda c: c[:3])
    out = []
    for _, _, age, r in candidates:
        key = (r["company"], title_key(r["title"]))
        if key in titles or per_company[r["company"]] >= cap:
            continue
        titles.add(key)
        per_company[r["company"]] += 1
        out.append(r | {"age": age})
        if len(out) >= n:
            break
    return out


def connect_note(item: dict, profile: dict) -> str:
    pitch = (profile.get("pitch") or {}).get(item["resume"], "")
    note = (f"Hi [Name], I applied for {item['title']} at {item['company']}. {pitch} "
            "Could I ask you one question about the team?")
    return " ".join(note.split())


def follow_up_note(app: dict, profile: dict) -> str:
    pitch = (profile.get("pitch") or {}).get(resume_variant(app["role"], profile), "")
    on = _d(app["applied_on"])
    sent = f"{on:%b} {on.day}" if on != date.min else "recently"
    note = (f"Hi [Name], a quick follow-up on my application for the {clean_title(app['title'])} role "
            f"at {app['company']}, sent {sent}. {pitch} I'd be glad to talk if the role is still open.")
    return " ".join(note.split())


def people_links(company: str, variant: str, profile: dict) -> list[list[str]]:
    links = []
    school = profile.get("school_linkedin")
    if school:
        links.append(["Alumni there", f"https://www.linkedin.com/school/{school}/people/?keywords={quote(company)}"])
    links.append(["Recruiters", "https://www.linkedin.com/search/results/people/?keywords="
                  + quote(f'"{company}" (recruiter OR "talent acquisition")')])
    links.append(["Hiring managers", "https://www.linkedin.com/search/results/people/?keywords="
                  + quote(f'"{company}" ({MANAGER_WORDS.get(variant, MANAGER_WORDS["ai-engineer"])})')])
    # STEM OPT needs an E-Verify employer; the public search has no deep links, so search the name there.
    links.append(["E-Verify check", "https://www.e-verify.gov/e-verify-employer-search"])
    return links


# Searches you open yourself on the big job sites, limited to the last 24 hours and newest first. Shortlist never
# reads LinkedIn or Indeed: their terms forbid automated access, so these are plain links for you to click.
SEARCH_ROLES = ["Data Engineer", "Data Analyst", "Data Scientist", "Machine Learning Engineer", "AI Engineer",
                "Analytics Engineer", "Business Analyst"]


def search_links(roles: list[str] | None = None, location: str = "United States") -> list[list]:
    out = []
    for role in roles or SEARCH_ROLES:
        q = quote(role)
        out.append([role, [
            ["LinkedIn", f"https://www.linkedin.com/jobs/search/?keywords={q}&location={quote(location)}&f_TPR=r86400&f_E=2%2C3&sortBy=DD"],
            ["Indeed", f"https://www.indeed.com/jobs?q={q}&l={quote(location)}&fromage=1&sort=date"],
            ["Google Jobs", f"https://www.google.com/search?q={quote(role + ' jobs')}&ibp=htl;jobs&htichips=date_posted:today"],
        ]])
    return out


# Fields to group the queue by, in the page's order, each with the angle to lead with there. Startups come first,
# as they do in the picking; cap-exempt employers have no group of their own, since few post these roles.
FIELDS = [
    ("startup", "Startups",
     "Lead with shipping: Scrapitch, built and launched solo, and the 10-agent Crisis Response system."),
    ("big", "Established companies",
     "Lead with Amazon and scale: 1M+ rows and dashboards for 5,000+ users. Most of these file H-1Bs for these roles; each card shows how many."),
    ("healthcare", "Healthcare and life sciences",
     "Lead with careful data work: validation and quality checks at Amazon, and reporting KPIs defined with academic leadership."),
    ("finance", "Finance and fintech",
     "Lead with accuracy at scale: ETL over 1M+ rows, data validation, and inventory and sales reporting."),
]
def is_startup(company: str, board: str) -> bool:
    """A startup is young and small: founded in the last 10 years with at most 500 people, going by
    company_facts.yaml (startups.py; both limits can be set in profile.yaml). An employer that hires through Workday,
    one established.yaml lists, and one whose founding year or headcount isn't known yet never counts as one."""
    return startups.is_startup(company, board)


_HEALTH_NAME = re.compile(r"health|medical|hospital|clinic|pharma|\bbio|therapeut|life science|children's|medicine|\bcare\b|iqvia|amgen|"
                          r"elevance|humana|cigna|aetna|kaiser|\bcvs\b|optum|pfizer|merck|moderna|biogen|hcsc|blue cross|anthem", re.I)
_HEALTH_JD = re.compile(r"\b(patients?|clinical|clinicians?|healthcare|health care|hipaa|pharma\w*|biotech\w*|life sciences|medical|"
                        r"hospitals?|payers?|ehr|fhir|drug discovery)\b", re.I)
_FIN_NAME = re.compile(r"bank|financ|capital|credit|invest|asset|wealth|securities|insurance|mutual|payments?|\bpay\b|\bvisa\b|mastercard|"
                       r"stripe|affirm|robinhood|fidelity|schwab|goldman|morgan|chase|citi|american express|paypal|block inc|coinbase|"
                       r"brex|\bramp\b|plaid|chime|sofi|blackrock|state street|liberty mutual|progressive|allstate|travelers|metlife|"
                       r"prudential|rocket companies|invesco|guidewire", re.I)
_FIN_JD = re.compile(r"\b(banking|banks?|financial services|fintech|payments?|trading|lending|loans?|mortgages?|insurance|underwriting|"
                     r"wealth management|asset management|brokerage|credit cards?)\b", re.I)


def field_of(company: str, title: str, jd: str, board: str) -> str:
    """Which field a job belongs to: startups first, whatever they work on; then established employers in
    healthcare or finance, by the employer's name or how much the posting talks about them; then the rest."""
    if is_startup(company, board):
        return "startup"
    text = f"{title}\n{jd}"
    if _HEALTH_NAME.search(company) or len(_HEALTH_JD.findall(text)) >= 4:
        return "healthcare"
    if _FIN_NAME.search(company) or len(_FIN_JD.findall(text)) >= 5:
        return "finance"
    return "big"


def build_items(picked: list[dict], profile: dict, messages: int = 20) -> list[dict]:
    resumes = profile.get("resumes") or {}
    have = profile.get("years_experience")
    items = []
    for i, r in enumerate(picked, 1):
        variant = resume_variant(r["role"], profile)
        salary = int(float(r["salary"])) if (r.get("salary") or "").replace(".", "").isdigit() else None
        item = {
            "n": i, "company": r["company"], "title": clean_title(r["title"]), "role": r["role"],
            "url": r["url"], "location": r["location"], "age": r["age"], "age_hours": r.get("age_hours"), "score": float(r["score"]),
            "sponsor": r.get("sponsor_reason", ""), "lottery": r.get("lottery_odds", ""),
            "wage_level": r.get("wage_level", ""), "years": r.get("years_required", ""), "have": have,
            "salary": salary, "salary_source": r.get("salary_source", ""),
            "mirror": labels(r.get("matched", "")), "missing": labels(r.get("missing", "")),
            "leverage": _mention(r.get("leverage", "")), "resume": variant, "resume_path": resumes.get(variant, ""),
            "people": people_links(r["company"], variant, profile),
        }
        item["note"] = connect_note(item, profile)
        item["message"] = i <= messages
        item["grad_check"] = bool(GRAD_PROGRAM.search(r["title"]))
        item["matched_keys"] = (r.get("matched") or "").split()
        item["board"] = board_of(r["url"])
        item["field"] = field_of(r["company"], r["title"], posting_text(r["url"]), item["board"])
        items.append(item)
    return items


def make_queue(profile: dict, n=30, spares=10, cap=2, max_age=60, min_score=3.0, messages=20,
               max_age_hours: float | None = 24) -> dict:
    from .roles import off_field
    # a scan made before a field rule changed still keeps to your degree's field
    rows = [r for r in _read(POSTINGS) if not off_field(r.get("title", ""), profile.get("roles") or [])]
    apps, skips = _read(APPS, APP_FIELDS), _read(SKIPS, SKIP_FIELDS)
    offset = (date.today() - datetime.fromtimestamp(POSTINGS.stat().st_mtime).date()).days if POSTINGS.exists() else 0
    exclude = tuple(EXCLUDE_TITLES) + tuple(profile.get("exclude_titles") or ())
    picked = pick(rows, apps, skips, n + spares, cap, max_age, min_score, age_offset=offset, exclude_titles=exclude,
                  max_age_hours=max_age_hours)
    items = build_items(picked, profile, messages)
    for it in items:
        it["spare"] = it["n"] > n
    queue = {"date": date.today().isoformat(), "made_at": datetime.now().isoformat(timespec="minutes"),
             "target": n, "items": items, "hours": max_age_hours,
             "results_from": datetime.fromtimestamp(POSTINGS.stat().st_mtime).isoformat(timespec="minutes")
             if POSTINGS.exists() else None}
    RESULTS.mkdir(parents=True, exist_ok=True)
    tmp = TODAY.with_suffix(".tmp")
    tmp.write_text(json.dumps(queue, indent=1))
    tmp.replace(TODAY)  # the page and Tailor never read half a queue
    return queue


# ---------- the tracker ----------

def record(company: str, title: str, url: str, role: str = "", channel: str = "cold",
           contact: str = "", notes: str = "", on: date | None = None) -> dict:
    on = on or date.today()
    if channel not in outcomes.CHANNELS:
        raise ValueError(f"channel must be one of {outcomes.CHANNELS}")
    with _lock:
        apps = _read(APPS, APP_FIELDS)
        if any(a["url"] == url for a in apps if url):
            return next(a for a in apps if a["url"] == url)
        due = [add_business_days(on, d).isoformat() for d in FOLLOW_UP_BUSINESS_DAYS] + ["", ""]
        row = {"applied_on": on.isoformat(), "company": company, "title": clean_title(title), "role": role,
               "url": url, "channel": channel, "contact": contact, "status": "applied", "status_on": on.isoformat(),
               "follow_up_1": due[0], "follow_up_1_done": "", "follow_up_2": due[1], "follow_up_2_done": "",
               "notes": notes}
        _write(APPS, APP_FIELDS, apps + [row])
    return row


def undo(url: str) -> bool:
    with _lock:
        apps = _read(APPS, APP_FIELDS)
        kept = [a for a in apps if a["url"] != url]
        skips = _read(SKIPS, SKIP_FIELDS)
        kept_skips = [s for s in skips if s["url"] != url]
        _write(APPS, APP_FIELDS, kept)
        _write(SKIPS, SKIP_FIELDS, kept_skips)
    return len(kept) != len(apps) or len(kept_skips) != len(skips)


def skip(company: str, title: str, url: str, reason: str) -> None:
    with _lock:
        rows = _read(SKIPS, SKIP_FIELDS)
        if not any(s["url"] == url for s in rows):
            rows.append({"skipped_on": date.today().isoformat(), "company": company, "title": title,
                         "url": url, "reason": reason if reason in SKIP_REASONS else "other"})
            _write(SKIPS, SKIP_FIELDS, rows)


def update(url: str, outcome: str) -> dict | None:
    """Set an application's status. Responses and rejections also feed the learning loop in outcomes.py."""
    if outcome not in STATUSES:
        raise ValueError(f"outcome must be one of {STATUSES}")
    with _lock:
        apps = _read(APPS, APP_FIELDS)
        hit = next((a for a in apps if a["url"] == url), None)
        if not hit:
            return None
        hit["status"], hit["status_on"] = outcome, date.today().isoformat()
        _write(APPS, APP_FIELDS, apps)
    if outcome in outcomes.OUTCOMES and hit["role"]:
        outcomes.log(hit["company"], hit["title"], hit["role"], hit["channel"] or "cold", outcome)
    return hit


def followed_up(url: str, which: int) -> None:
    with _lock:
        apps = _read(APPS, APP_FIELDS)
        for a in apps:
            if a["url"] == url:
                a[f"follow_up_{which}_done"] = date.today().isoformat()
        _write(APPS, APP_FIELDS, apps)


def followups_due(profile: dict, today: date | None = None) -> list[dict]:
    today = today or date.today()
    due = []
    for a in _read(APPS, APP_FIELDS):
        if a["status"] != "applied":
            continue
        for which in (1, 2):
            if a[f"follow_up_{which}"] and not a[f"follow_up_{which}_done"] and _d(a[f"follow_up_{which}"]) <= today:
                due.append(a | {"which": which, "note": follow_up_note(a, profile)})
                break
    return due


# ---------- the local page ----------

def _state(profile: dict) -> dict:
    apps = _read(APPS, APP_FIELDS)
    today = date.today().isoformat()
    return {"applied": {a["url"]: a for a in apps}, "skipped": [s["url"] for s in _read(SKIPS, SKIP_FIELDS)],
            "applied_today": sum(a["applied_on"] == today for a in apps),
            "followups": followups_due(profile), "recent": [a for a in apps if _d(a["applied_on"]) >= date.today() - timedelta(days=21)]}


def serve(profile: dict, port: int = 8765, open_browser: bool = True) -> None:
    token = secrets.token_urlsafe(24)
    tailor = tailor_url(profile) or ""
    for candidate in range(port, port + 10):  # a page from an earlier run may still hold the port
        if candidate != port and tailor.endswith(f":{candidate}/"):
            continue  # never Tailor's port: the cards' links point there
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), BaseHTTPRequestHandler)
        except OSError:
            continue
        httpd.server_close()
        port = candidate
        break
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{h}" for h in hosts}

    class Handler(BaseHTTPRequestHandler):
        server_version = "shortlist"

        def log_message(self, *a):  # keep the terminal quiet
            pass

        def _send(self, code, body: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                             "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _host_ok(self):
            return self.headers.get("Host", "") in hosts

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, b'{"error":"forbidden"}')
            if self.path == "/":
                queue = json.loads(TODAY.read_text()) if TODAY.exists() else {"items": [], "target": 0}
                return self._send(200, page(queue, _state(profile), token, tailor_url(profile)).encode(),
                                  "text/html; charset=utf-8")
            if self.path == "/api/state":
                return self._send(200, json.dumps(_state(profile)).encode())
            return self._send(404, b'{"error":"not found"}')

        def do_POST(self):
            origin = self.headers.get("Origin")
            if not self._host_ok() or self.headers.get("X-Token") != token or (origin and origin not in origins):
                return self._send(403, b'{"error":"forbidden"}')
            length = int(self.headers.get("Content-Length") or 0)
            if length > 200_000:
                return self._send(413, b'{"error":"too large"}')
            try:
                d = json.loads(self.rfile.read(length) or b"{}")
                url = str(d.get("url", ""))[:2000]
                if self.path == "/api/applied":
                    record(str(d.get("company", ""))[:200], str(d.get("title", ""))[:300], url,
                           str(d.get("role", ""))[:40], str(d.get("channel") or "cold"), str(d.get("contact", ""))[:200])
                elif self.path == "/api/skip":
                    skip(str(d.get("company", ""))[:200], str(d.get("title", ""))[:300], url, str(d.get("reason", "")))
                elif self.path == "/api/undo":
                    undo(url)
                elif self.path == "/api/followed":
                    followed_up(url, 2 if d.get("which") == 2 else 1)
                elif self.path == "/api/status":
                    update(url, str(d.get("status", "")))
                elif self.path == "/api/refresh":
                    return self._send(200, json.dumps(start_refresh(profile, d.get("hours", 24))).encode())
                elif self.path == "/api/refresh/status":
                    return self._send(200, json.dumps(dict(REFRESH)).encode())
                elif self.path == "/api/linkedin/check":
                    return self._send(200, json.dumps(linkedin_check(d, profile)).encode())
                else:
                    return self._send(404, b'{"error":"not found"}')
            except (ValueError, KeyError, subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as exc:
                return self._send(400, json.dumps({"error": str(exc)}).encode())
            return self._send(200, json.dumps(_state(profile)).encode())

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"\ntoday's queue: {url}  (Ctrl+C to stop; everything is saved as you go)", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()


def tailor_url(profile: dict) -> str | None:
    """Where Tailor's own page is (python -m tailor), for the links on the cards: `tailor_port` in profile.yaml,
    else 8766. None until `resume_facts` is set, since Tailor has nothing to tailor from before that."""
    if not profile.get("resume_facts"):
        return None
    try:
        port = int(profile.get("tailor_port") or 8766)
    except (TypeError, ValueError):  # a bad port in profile.yaml shouldn't take the queue page down
        port = 8766
    return f"http://127.0.0.1:{port}/"


def page(queue: dict, state: dict, token: str, tailor: str | None = None) -> str:
    """The queue page. `tailor` is Tailor's address (see tailor_url), or None to leave its links off."""
    data = json.dumps({"queue": queue, "state": state, "token": token, "reasons": SKIP_REASONS,
                       "statuses": STATUSES, "tailor": tailor, "fields": FIELDS,
                       "searches": [[role, [x for x in links if x[0] != "LinkedIn"]] for role, links in search_links()],
                       "linkedin": linkedin.search_links(), "refresh": dict(REFRESH)}).replace("<", "\\u003c")
    return PAGE.replace("/*DATA*/null", data)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Today's applications</title>
<style>
:root{--bg:#f5f5f7;--card:#fff;--ink:#1d1d1f;--sub:#6e6e73;--line:#e3e3e8;--blue:#0071e3;--green:#1f8f4e;--amber:#b25e00;--chip:#f0f0f3}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro Text","Inter",system-ui,sans-serif}
header{position:sticky;top:0;z-index:5;background:rgba(245,245,247,.86);backdrop-filter:saturate(180%) blur(18px);border-bottom:1px solid var(--line)}
.wrap{max-width:980px;margin:0 auto;padding:0 16px}
.bar{display:flex;align-items:center;gap:16px;padding:14px 0;flex-wrap:wrap}
h1{font-size:22px;letter-spacing:-.01em;margin:0}
.meter{flex:1;min-width:160px;height:8px;background:var(--line);border-radius:99px;overflow:hidden}.meter i{display:block;height:100%;background:var(--green);width:0;transition:width .3s}
.count{font-weight:600}.muted{color:var(--sub)}
h2{font-size:17px;margin:28px 0 10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px 18px;margin:0 0 12px}
.card.done{opacity:.55}.card.done .actions .apply{background:var(--green)}
.top{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.n{font-weight:700;color:var(--sub);min-width:26px}.co{font-weight:700}.ti a{color:var(--ink);text-decoration:none;font-weight:600}.ti a:hover{color:var(--blue)}
.chip{display:inline-block;background:var(--chip);border-radius:99px;padding:2px 9px;font-size:12.5px;color:var(--ink)}
.chip.new{background:#e7f6ec;color:var(--green);font-weight:600}.chip.warn{background:#fff2e0;color:var(--amber)}
.facts{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.row{margin:6px 0;font-size:14px}.row b{font-weight:600}
textarea{width:100%;min-height:64px;border:1px solid var(--line);border-radius:10px;padding:8px 10px;font:inherit;font-size:14px;resize:vertical}
input[type=text]{border:1px solid var(--line);border-radius:8px;padding:6px 8px;font:inherit;font-size:14px}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;align-items:center}
button,.btn{border:0;border-radius:99px;padding:7px 14px;font:inherit;font-size:14px;cursor:pointer;background:var(--chip);color:var(--ink);text-decoration:none}
button.primary,.btn.primary{background:var(--blue);color:#fff}button.apply{background:var(--ink);color:#fff}
select{border:1px solid var(--line);border-radius:99px;padding:6px 10px;font:inherit;font-size:14px;background:#fff}
.links a{margin-right:12px;color:var(--blue);text-decoration:none;font-size:14px}
details summary{cursor:pointer;color:var(--sub);margin:8px 0}
.small{font-size:12.5px}.ok{color:var(--green)}.err{color:#c00}.warn-row{color:var(--amber)}
.field h3{font-size:16px;margin:22px 0 2px}.field p{margin:0 0 10px}
.li-roles{display:flex;flex-wrap:wrap;gap:6px 18px;margin:8px 0;font-size:14px}.li-roles a{margin-left:7px;color:var(--blue);text-decoration:none}
</style></head><body>
<header><div class="wrap bar"><h1>Today</h1><div class="meter"><i id="meter"></i></div>
<div><span class="count" id="count">0</span> <span class="muted">applied today</span></div>
<label class="small muted">Group by <select id="group"><option value="field">field</option><option value="rank">rank only</option><option value="board">job board</option></select></label>
<button id="refresh">Refresh</button><label class="small muted">last <input type="number" id="hours" min="24" max="48" step="1" value="24" aria-label="Hours to keep, 24 to 48" style="width:60px;border:1px solid var(--line);border-radius:8px;padding:5px 6px;font:inherit;font-size:14px"> hours</label>
<button class="primary" id="next">Open next job</button><span class="links" id="tailor-nav" hidden></span></div></header>
<main class="wrap">
<div id="status" class="small muted"></div>
<div id="fresh" class="small muted" style="margin:10px 0 0"></div>
<section id="fu"></section>
<div class="card" id="li"><div class="row"><b>LinkedIn, last 24 hours</b> <span class="small muted">Full-time, entry and associate level, newest first. These open in your browser; Shortlist never opens LinkedIn.</span></div>
<div id="li-links"></div>
<div class="row small muted">Then copy the page and paste it here: click in the results, press Cmd+A, then Cmd+C. Each job is checked for H-1B filings and for the posting on the company's own site.</div>
<textarea id="li-text" placeholder="Paste a LinkedIn job search or hiring posts page"></textarea>
<div class="actions"><button class="primary" id="li-go">Check these jobs</button></div>
<div id="li-out"></div></div>
<details class="card" id="boards"><summary><b>Search Indeed and Google Jobs yourself: posted in the last 24 hours</b></summary><div id="searches"></div><p class="small muted">Shortlist reads employers' own job boards. These sites forbid automated reading, so these are links for you to open; each shows only jobs from the last 24 hours, newest first.</p></details>
<h2>Queue</h2><div id="queue"></div>
<details><summary>Spares, if you skip some</summary><div id="spares"></div></details>
<h2>Applied in the last 3 weeks</h2><div id="recent"></div>
</main>
<script>
const D=/*DATA*/null;let S=D.state;const Q=D.queue;
const $=(id)=>document.getElementById(id);
const STALE='This page is from before the page server restarted. Reload it (Command+R), then try again.';
function el(tag,attrs,...kids){const e=document.createElement(tag);for(const[k,v]of Object.entries(attrs||{})){if(k==='text')e.textContent=v;else if(k==='href'){if(/^https:\\/\\//.test(v)||(D.tailor&&String(v).startsWith(D.tailor)))e.href=v;}else if(k.startsWith('on'))e.addEventListener(k.slice(2),v);else e.setAttribute(k,v);}for(const k of kids){if(k!=null)e.append(k);}return e;}
async function post(path,body){try{const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Token':D.token},body:JSON.stringify(body)});if(!r.ok)throw new Error(r.status===403?STALE:((await r.json()).error||r.status));S=await r.json();render();flash('Saved','ok');}catch(e){flash(e.message===STALE?STALE:'Not saved: '+e.message+'. Is `python -m shortlist today` still running?','err');}}
function flash(t,c){const s=$('status');s.className='small '+c;s.textContent=t;clearTimeout(flash.t);flash.t=setTimeout(()=>{s.textContent='';},c==='err'?9000:2500);}
function copy(text){  /* the clipboard, then the older copy command, then the text selected for you to copy */
 const old=()=>{const t=el('textarea',{style:'position:fixed;top:0;left:0;width:1px;height:1px;opacity:0'});t.value=text;document.body.append(t);t.select();
   let done=false;try{done=document.execCommand('copy');}catch(e){}t.remove();return done;};
 const show=()=>{const t=el('textarea',{rows:'12',style:'position:fixed;inset:10% 10% auto 10%;width:80%;z-index:9;box-shadow:0 8px 40px rgba(0,0,0,.25)'});t.value=text;
   const close=el('button',{class:'apply',text:'Done',style:'position:fixed;z-index:10;right:10%;top:calc(10% + 260px)',onclick:()=>{t.remove();close.remove();}});
   document.body.append(t,close);t.focus();t.select();flash('Press Command+C to copy','ok');};
 (navigator.clipboard?navigator.clipboard.writeText(text):Promise.reject()).then(()=>flash('Copied','ok'),()=>{if(old())flash('Copied','ok');else show();});}
function ago(a,h){if(h!=null&&h<48)return h<1?'posted under an hour ago':'posted '+h+(h===1?' hour':' hours')+' ago';return a===0?'posted today':a===1?'posted 1 day ago':'posted '+a+' days ago';}
/* A queue's ages are counted when it's picked (Q.made_at); a page kept open, or a queue picked on an earlier day, adds the time since */
function ageNow(it){const t=new Date(Q.made_at||''),now=new Date();if(isNaN(t))return {days:it.age,hours:it.age_hours};
 const hours=it.age_hours==null?null:Math.round(it.age_hours+Math.max(0,now-t)/36e5),day=d=>new Date(d.getFullYear(),d.getMonth(),d.getDate());
 return {hours,days:hours!=null?Math.floor(hours/24):it.age+Math.max(0,Math.round((day(now)-day(t))/864e5))};}  /* Workday gives dates only: whole days */
function card(it){
 const applied=S.applied[it.url],skipped=S.skipped.includes(it.url),age=ageNow(it);
 const facts=el('div',{class:'facts'},
  el('span',{class:'chip'+(age.days<=3?' new':''),text:ago(age.days,age.hours)}),
  el('span',{class:'chip',text:'score '+it.score.toFixed(0)}),
  el('span',{class:'chip',text:it.board}),
  it.sponsor?el('span',{class:'chip',text:'Sponsor: '+it.sponsor}):null,
  it.lottery?el('span',{class:'chip',text:'Lottery '+Math.round(100*it.lottery)+'%'+(it.wage_level&&it.wage_level!=='?'?' (wage level '+it.wage_level+')':'')}):null,
  it.years?el('span',{class:'chip'+(Number(it.years)>Number(it.have)?' warn':''),text:'asks '+it.years+' yrs, you have '+it.have}):null,
  it.salary?el('span',{class:'chip',text:(it.salary_source==='posted'?'posted pay midpoint $':'typical pay $')+it.salary.toLocaleString()}):null);
 const name=el('input',{type:'text',placeholder:'Their first name',size:'14'});
 const note=el('textarea',{}); note.value=it.note;
 const cnt=el('span',{class:'small muted'});
 const upd=()=>{cnt.textContent=note.value.length+' characters'+(note.value.length>200?' (free LinkedIn notes allow 200)':'');};
 name.addEventListener('input',()=>{note.value=it.note.replace('[Name]',name.value.trim()||'[Name]');upd();});note.addEventListener('input',upd);upd();
 const reason=el('select',{},el('option',{value:'',text:'Skip because...'}),...D.reasons.map(r=>el('option',{value:r,text:r})));
 reason.addEventListener('change',()=>{if(reason.value)post('/api/skip',{url:it.url,company:it.company,title:it.title,reason:reason.value});});
 const channel=el('select',{},...['cold','referral','direct','recruiter','event'].map(c=>el('option',{value:c,text:c==='cold'?'applied on their site':c})));
 const actions=el('div',{class:'actions'},
  el('a',{class:'btn primary',href:it.url,target:'_blank',rel:'noopener noreferrer',text:'Open job'}),
  applied||skipped?el('button',{onclick:()=>post('/api/undo',{url:it.url}),text:applied?'Applied. Undo':'Skipped. Undo'}):
   el('button',{class:'apply',onclick:()=>post('/api/applied',{url:it.url,company:it.company,title:it.title,role:it.role,channel:channel.value,contact:name.value.trim()}),text:'Mark applied'}),
  applied||skipped?null:channel, applied||skipped?null:reason);
 return el('div',{class:'card'+(applied||skipped?' done':''),id:'c'+it.n},
  el('div',{class:'top'},el('span',{class:'n',text:'#'+it.n}),el('span',{class:'co',text:it.company}),el('span',{class:'ti'},el('a',{href:it.url,target:'_blank',rel:'noopener noreferrer',text:it.title})),el('span',{class:'muted small',text:it.location})),
  facts,
  el('div',{class:'row'},el('b',{text:'Resume: '}),document.createTextNode(it.resume+' version  '),it.resume_path?el('button',{onclick:()=>copy(it.resume_path),text:'Copy file path'}):el('span',{class:'muted small',text:'(set resumes in profile.yaml)'}),
   D.tailor?' ':null,D.tailor?el('a',{class:'btn',href:D.tailor+'?job='+encodeURIComponent(it.url),target:'tailor',text:'Tailor this job'}):null),
  it.mirror.length?el('div',{class:'row'},el('b',{text:'Use their words: '}),document.createTextNode(it.mirror.join(', '))):null,
  it.missing.length?el('div',{class:'row muted'},el('b',{text:'They also want: '}),document.createTextNode(it.missing.join(', ')+'. Mention only what you have really used.')):null,
  it.leverage?el('div',{class:'row'},el('b',{text:'Your angle: '}),document.createTextNode(it.leverage)):null,
  it.grad_check?el('div',{class:'row warn-row'},el('b',{text:'Graduate program: '}),document.createTextNode('check the graduation window before applying. Many 2027 programs want Dec 2026 to mid-2027 grads.')):null,
  age.days>7?el('div',{class:'row warn-row'},el('b',{text:'Over a week old: '}),document.createTextNode('the first reviews may be done. A note to someone there matters more than usual.')):null,
  el('div',{class:'row'},el('b',{text:it.message?'Message one person: ':'Optional, apply-only is fine (invite limit): '}),el('span',{class:'links'},...it.people.map(([t,u])=>el('a',{href:u,target:'_blank',rel:'noopener noreferrer',text:t})))),
  el('div',{class:'row'},name,' ',el('button',{onclick:()=>copy(note.value),text:'Copy message'}),' ',cnt),note,
  actions);
}
function fuCard(a){
 const note=el('textarea',{});note.value=a.note;
 return el('div',{class:'card'},el('div',{class:'top'},el('span',{class:'co',text:a.company}),el('span',{class:'ti'},el('a',{href:a.url,target:'_blank',rel:'noopener noreferrer',text:a.title})),el('span',{class:'muted small',text:'applied '+a.applied_on+(a.contact?', contact: '+a.contact:'')})),
  note,el('div',{class:'actions'},el('button',{onclick:()=>copy(note.value),text:'Copy follow-up'}),el('button',{class:'apply',onclick:()=>post('/api/followed',{url:a.url,which:a.which}),text:'Mark followed up'}),
  el('button',{onclick:()=>post('/api/status',{url:a.url,status:'no_response'}),text:'No reply, close it'})));
}
function recentRow(a){
 const sel=el('select',{},...D.statuses.map(s=>el('option',{value:s,text:s})));sel.value=a.status;
 sel.addEventListener('change',()=>post('/api/status',{url:a.url,status:sel.value}));
 return el('div',{class:'row'},el('span',{class:'muted small',text:a.applied_on+'  '}),el('b',{text:a.company+': '}),el('a',{href:a.url,target:'_blank',rel:'noopener noreferrer',text:a.title}),' ',sel);
}
function render(){
 const mode=$('group').value,byBoard=(a,b)=>mode==='board'?(a.board.localeCompare(b.board)||a.n-b.n):a.n-b.n;
 const main=Q.items.filter(i=>!i.spare).sort(byBoard),sp=Q.items.filter(i=>i.spare).sort(byBoard);
 let rows=main.map(card);
 if(mode==='field'&&main.length){rows=[];const known=new Set((D.fields||[]).map(f=>f[0])),fieldOf=i=>known.has(i.field)?i.field:'big';  // a queue picked before a field was renamed
  for(const [key,name,tip] of (D.fields||[])){const inF=main.filter(i=>fieldOf(i)===key);if(!inF.length)continue;
  rows.push(el('div',{class:'field'},el('h3',{text:name+' ('+inF.length+')'}),el('p',{class:'small muted',text:tip})),...inF.map(card));}}
 $('queue').replaceChildren(...(main.length?rows:[el('p',{class:'muted',text:'Nothing left to pick. Run a fresh scan, or add employers.'})]));
 $('spares').replaceChildren(...sp.map(card));
 const fu=S.followups||[];
 $('fu').replaceChildren(...(fu.length?[el('h2',{text:'Follow-ups due ('+fu.length+')'}),...fu.map(fuCard)]:[]));
 $('recent').replaceChildren(...((S.recent||[]).slice().reverse().map(recentRow)));
 $('searches').replaceChildren(...(D.searches||[]).map(([role,links])=>el('div',{class:'row'},el('b',{text:role+': '}),el('span',{class:'links'},...links.map(([site,u])=>el('a',{href:u,target:'_blank',rel:'noopener noreferrer',text:site}))))));
 $('count').textContent=S.applied_today+' of '+Q.target;
 $('meter').style.width=Math.min(100,100*S.applied_today/Math.max(1,Q.target))+'%';
}
$('next').addEventListener('click',()=>{const it=Q.items.find(i=>!S.applied[i.url]&&!S.skipped.includes(i.url));if(it){$('c'+it.n).scrollIntoView({behavior:'smooth',block:'start'});if(/^https:\\/\\//.test(it.url))window.open(it.url,'_blank','noopener');}});
$('group').addEventListener('change',render);
/* Refresh: every employer's job board read again, then today's queue from the last 24 hours */
const when=s=>{if(!s)return '';const d=new Date(s);return isNaN(d)?s:d.toLocaleString([],{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});};
const HOURS=[24,48],hoursBox=$('hours');  /* the Refresh window: 24 to 48 hours, remembered in this browser */
try{const v=localStorage.getItem('refreshHours');if(v)hoursBox.value=v;}catch(e){}
const hours=()=>{const v=Number(hoursBox.value);return Number.isInteger(v)&&v>=HOURS[0]&&v<=HOURS[1]?v:null;};
function idleText(){const t=$('fresh');t.className='small muted';
 t.textContent=Q.results_from?'Jobs from the last '+(Q.hours||24)+' hours, read '+when(Q.results_from)+'. Refresh reads every employer\\'s job board again and keeps the last '+(hours()||HOURS[0])+' hours.':'';}
function showRefresh(r){const b=$('refresh'),t=$('fresh');
 if(r.running){b.disabled=hoursBox.disabled=true;b.textContent='Refreshing...';t.className='small muted';
  t.textContent=(r.stage||'Starting')+(r.total&&r.stage==='Reading job boards'?': '+r.done+' of '+r.total+' job boards':'')+', keeping the last '+(r.hours||24)+' hours. About 10 minutes; keep applying meanwhile.';return true;}
 b.disabled=hoursBox.disabled=false;b.textContent='Refresh';
 if(r.error){t.className='small err';t.textContent='The refresh stopped ('+r.error+'). Your queue is unchanged.';return false;}
 idleText();return false;}
hoursBox.addEventListener('change',()=>{if(hours()==null){flash('Pick '+HOURS[0]+' to '+HOURS[1]+' hours','err');
  hoursBox.value=Math.min(HOURS[1],Math.max(HOURS[0],Math.round(Number(hoursBox.value))||HOURS[0]));}
 try{localStorage.setItem('refreshHours',hoursBox.value);}catch(e){}if(!$('refresh').disabled)idleText();});
async function pollRefresh(){let r;try{r=await api('/api/refresh/status',{});}catch(e){flash(e.message,'err');return;}
 if(showRefresh(r)){setTimeout(pollRefresh,3000);return;}
 if(!r.error&&r.found!=null)$('fresh').replaceChildren('New listings are ready: '+r.found+' jobs from the last '+(r.hours||24)+' hours. ',el('button',{class:'primary',text:'Show them',onclick:()=>location.reload()}));}
$('refresh').addEventListener('click',async()=>{const v=hours();if(v==null){flash('Pick '+HOURS[0]+' to '+HOURS[1]+' hours','err');return;}
 try{showRefresh(await api('/api/refresh',{hours:v}));setTimeout(pollRefresh,2000);}catch(e){flash(e.message,'err');}});
showRefresh(D.refresh||{});if((D.refresh||{}).running)setTimeout(pollRefresh,2000);
/* LinkedIn: searches you open yourself, and the jobs in a page you copied */
$('li-links').replaceChildren(el('div',{class:'li-roles'},...(D.linkedin||[]).map(x=>el('span',{},el('b',{text:x.role}),
  el('a',{href:x.jobs,target:'_blank',rel:'noopener noreferrer',text:'listings'}),x.posts?el('a',{href:x.posts,target:'_blank',rel:'noopener noreferrer',text:'hiring posts'}):null))));
let LI_LINKS=[];
function liLinks(html){const out=[],seen=new Set();if(!html)return out;
 const doc=new DOMParser().parseFromString(html,'text/html');  /* an inert copy: nothing in it runs or loads */
 for(const a of doc.querySelectorAll('a[href]')){const h=a.getAttribute('href')||'';let u='',m=h.match(/\\/jobs\\/view\\/(?:[^\\/?#]*?-)?(\\d{6,})/)||h.match(/[?&]currentJobId=(\\d{6,})/);
  if(m)u='https://www.linkedin.com/jobs/view/'+m[1]+'/';else{m=h.match(/urn:li:activity:(\\d{10,})/);if(m)u='https://www.linkedin.com/feed/update/urn:li:activity:'+m[1]+'/';}
  const t=(a.textContent||'').replace(/\\s+/g,' ').trim().slice(0,160);
  if(u&&t&&!seen.has(u)){seen.add(u);out.push({text:t,url:u});}if(out.length>=120)break;}
 return out;}
$('li-text').addEventListener('paste',e=>{LI_LINKS=liLinks(e.clipboardData&&e.clipboardData.getData('text/html'));});
$('li-go').addEventListener('click',async()=>{const b=$('li-go'),out=$('li-out');b.disabled=true;
 out.replaceChildren(el('p',{class:'small muted',text:'Reading the jobs and checking each company, about 30 seconds...'}));
 try{const r=await api('/api/linkedin/check',{text:$('li-text').value.slice(0,120000),links:LI_LINKS});out.replaceChildren(...liRows(r));}
 catch(e){out.replaceChildren(el('p',{class:'err',text:e.message}));}b.disabled=false;});
function liRows(r){if(!r.jobs.length)return [el('p',{class:'small muted',text:r.note||'No jobs found.'})];
 const sp=r.jobs.filter(j=>j.sponsor&&j.sponsor.filings>0).length;
 return [el('p',{class:'small',text:r.jobs.length+' jobs read, '+sp+' at companies with H-1B filings. Those come first.'}),...r.jobs.map(liCard)];}
function liCard(j){const s=j.sponsor,o=j.own;
 const facts=el('div',{class:'facts'},
  s&&s.filings?el('span',{class:'chip new',text:'H-1B: '+s.filings+' filings'+(s.yours?', '+s.yours+' in your roles':'')}):el('span',{class:'chip warn',text:s?'No H-1B filings found':'No H-1B record under this name'}),
  o&&o.n?el('span',{class:'chip new',text:'In your queue as #'+o.n}):null,
  o&&o.skipped?el('span',{class:'chip warn',text:'Shortlist skips it: '+o.skipped}):null,
  j.posted?el('span',{class:'chip',text:j.posted}):null,
  j.applied?el('span',{class:'chip',text:'Applied already'}):null);
 const mark=el('button',{class:'apply',text:'Mark applied',onclick:async()=>{mark.disabled=true;
   await post('/api/applied',{url:j.track_url,company:j.company,title:j.title,role:j.role,channel:'cold',contact:j.poster||''});
   if(S.applied&&S.applied[j.track_url])mark.textContent='Applied';else mark.disabled=false;}});
 return el('div',{class:'card'},
  el('div',{class:'top'},el('span',{class:'co',text:j.company}),el('span',{class:'ti',text:j.title}),el('span',{class:'muted small',text:j.location})),
  j.kind==='post'&&j.poster?el('div',{class:'row small'},'Posted by '+j.poster+(j.poster_title?', '+j.poster_title:'')+'. A message to them beats a cold application.'):null,
  facts,
  el('div',{class:'actions'},
   j.link?el('a',{class:'btn',href:j.link,target:'_blank',rel:'noopener noreferrer',text:j.kind==='post'?'Open the post':'Open on LinkedIn'}):null,
   o?el('a',{class:'btn primary',href:o.url,target:'_blank',rel:'noopener noreferrer',text:'Their own posting'}):el('a',{class:'btn',href:j.find,target:'_blank',rel:'noopener noreferrer',text:'Find it on their site'}),
   j.applied?null:mark));}
async function api(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Token':D.token},body:JSON.stringify(body)});const j=await r.json().catch(()=>({error:'No answer from the page. Is it still running?'}));if(!r.ok)throw new Error(r.status===403?STALE:(j.error||r.status));return j;}
if(D.tailor){const n=$('tailor-nav');n.hidden=false;n.append(el('a',{href:D.tailor,target:'tailor',text:'Open Tailor'}));}
render();
</script></body></html>
"""
