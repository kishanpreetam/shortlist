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

import yaml

from . import h1bdata, linkedin, outcomes
from . import resume as engine
from . import rewrite
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
SKILL_LABELS = {
    "ab_testing": "A/B testing", "agents": "AI agents", "aws": "AWS", "ci_cd": "CI/CD", "cpp": "C++",
    "data_modeling": "data modeling", "dbt": "dbt", "etl": "ETL", "evals": "LLM evals", "fastapi": "FastAPI",
    "gcp": "GCP", "llm": "LLMs", "mcp": "MCP", "ml": "machine learning", "nextjs": "Next.js", "nlp": "NLP",
    "nosql": "NoSQL", "postgres": "PostgreSQL", "power_bi": "Power BI", "pytorch": "PyTorch", "r_lang": "R",
    "rag": "RAG", "rest_api": "REST APIs", "scikit_learn": "scikit-learn", "sql": "SQL", "vector_db": "vector databases",
    "xgboost": "XGBoost", "javascript": "JavaScript", "typescript": "TypeScript", "tensorflow": "TensorFlow",
    "python": "Python", "airflow": "Airflow", "spark": "Spark", "tableau": "Tableau", "docker": "Docker", "git": "Git",
    "excel": "Excel", "react": "React", "kafka": "Kafka", "kubernetes": "Kubernetes", "django": "Django",
    "snowflake": "Snowflake", "databricks": "Databricks", "looker": "Looker", "terraform": "Terraform", "java": "Java",
    "scala": "Scala", "go": "Go", "linux": "Linux", "flask": "Flask", "langchain": "LangChain", "azure": "Azure",
    "deep_learning": "deep learning",
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
    for name, marks in (("Workday", ("myworkdayjobs.com",)), ("Ashby", ("ashbyhq.com",)), ("Lever", ("lever.co",)),
                        ("Greenhouse", ("greenhouse.io", "gh_jid="))):
        if any(m in u for m in marks):
            return name
    return "Other"


CHROME_DEFAULT = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PREVIEWS: dict[str, dict] = {}
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


def _facts_path(profile: dict) -> Path:
    path = profile.get("resume_facts")
    if not path:
        raise ValueError("Set resume_facts in profile.yaml to your facts file to tailor resumes.")
    return Path(str(path)).expanduser()


def variant_for_title(title: str) -> str:
    t = engine.choose_title(title, [t for _, t in engine.TITLE_RULES], "ai-engineer")
    return {"Analytics Engineer": "data-engineer", "Data Engineer": "data-engineer", "Machine Learning Engineer": "data-scientist",
            "Data Scientist": "data-scientist", "Business Analyst": "data-analyst", "Data Analyst": "data-analyst"}.get(t, "ai-engineer")


def _job_of(d: dict) -> tuple[str, str, int, str, str]:
    """The job a preview is for, from the queue or pasted: (company, title, n, resume version, description)."""
    if d.get("paste"):
        pst = d["paste"]
        company, title = str(pst.get("company", "")).strip()[:120], str(pst.get("title", "")).strip()[:200]
        jd = str(pst.get("jd", ""))[:60_000]
        if not company or not title or len(jd) < 200:
            raise ValueError("Add the company, the job title and the whole job description.")
        n, variant = 0, variant_for_title(title)
    else:
        queue = json.loads(TODAY.read_text()) if TODAY.exists() else {"items": []}
        item = next((i for i in queue["items"] if i["n"] == d.get("n")), None)
        if not item:
            raise ValueError("That job isn't in today's queue anymore. Reload the page.")
        company, title, n, variant = item["company"], item["title"], item["n"], item["resume"]
        jd = posting_text(item["url"])
        if len(jd) < 200:
            raise ValueError("The job description for this posting isn't saved. Paste it under Paste a job.")
    return company, title, n, variant, jd


def _summary_ctx(facts: dict, job_title: str, variant: str, phrases=()) -> dict:
    """What the summary written for a job starts from: its opening, your evidence, the tools you can name and the
    posting's keywords (`phrases`), which it uses in the posting's words where your evidence shows the work."""
    title = engine.choose_title(job_title, facts["titles"]["items"] if engine.ok(facts["titles"]) else [], variant)
    opening = engine.summary_opening(facts, title, variant)
    return {"opening": opening, "evidence": engine.summary_evidence(facts),
            "tools": sorted({engine.item_label(t) for t in engine.placed(facts) | engine.evidence(facts)}
                            | {it for x in facts.get("skills") or [] if engine.ok(x) for it in x["items"]}),
            "keywords": list(phrases)[:engine.KEYWORDS_MAX],
            "check": lambda text, uses: engine.summary_problems(text, facts, opening, uses, phrases)}


def _job_reading(facts: dict, title: str, jd: str, company: str, variant: str, claude: str | None) -> tuple[list, dict, list]:
    """Your points for one job, each with the posting's keywords for what it shows; the summary's context; and the
    posting's ranked keywords. The posting is read first (once, cached), so its keywords reach every rewrite."""
    given, reqs = [], []
    if claude and jd.strip():
        try:
            reqs = rewrite.requirements(title, jd, claude, RESULTS / "requirements")
            given = rewrite.keywords(title, jd, claude, RESULTS / "requirements")
        except rewrite.RewriteError:
            pass
    kws = engine.job_keywords(title, jd, given, company, reqs)
    return (rewrite.points_for(facts, title, jd, company, kws), _summary_ctx(facts, title, variant, [k["text"] for k in kws]), kws)


def resume_suggest(d: dict, profile: dict) -> dict:
    """Suggested lines for one requirement of a job: two or three, each for one of your roles or projects, labeled
    by what it adds beyond that place's facts, with one recommended (one that only rewords your lines when any does)."""
    facts = engine.load_facts(_facts_path(profile))
    company, title, n, variant, jd = _job_of(d)
    claude = rewrite.claude_path(profile)
    if not claude:
        raise ValueError("Suggestions need Claude Code installed and signed in (claude auth login).")
    requirement = " ".join(str(d.get("requirement") or "").split())[:200]
    if len(requirement) < 5:
        raise ValueError("Pick a requirement to suggest lines for.")
    places = engine.place_evidence(facts)
    own = {b["id"]: b["text"] for grp in ("roles", "projects") for g in facts[grp] for b in g["bullets"]}
    page = [own[i] for i in d.get("on_page") or [] if isinstance(i, str) and i in own]  # the lines this preview shows
    try:
        got = rewrite.suggest_lines(requirement, title, jd, places, claude, RESULTS / "suggestions", page)
    except rewrite.RewriteError as exc:
        raise ValueError(str(exc)) from exc
    names = {x["id"]: x["name"] for x in places}
    roles = {g["id"] for g in facts["roles"]}
    out = []
    for x in got:
        check = engine.suggestion_check(x["text"], facts, x["place"])
        if not check["hard"]:
            rank = engine.rank_draft(x["text"], requirement, page, x["place"] in roles)
            out.append({**x, "name": names.get(x["place"], ""), "adds": check["adds"], "grounded": not check["adds"], "rank": rank,
                        "in_experience": x["place"] in roles})
    # a draft the page already shows adds nothing; the recommendation then goes, in order, to one that addresses the
    # requirement at all, is true to your lines, names the skill the requirement names, and covers the most of it,
    # adds what the page lacks and lands in your experience (rank_draft's score)
    out = [o for o in out if o["rank"]["dup"] < 0.8]
    for o in out:  # how much you'd be confirming: nothing, a few words, or facts only you know (blanks or numbers)
        o["truth"] = 0 if o["grounded"] else 2 if any(a.startswith(("blanks", "numbers")) for a in o["adds"]) else 1
    strongest = min(out, key=lambda o: (o["rank"]["share"] == 0, -o["rank"]["named"], -o["rank"]["score"]), default=None)
    out.sort(key=lambda o: (o["rank"]["share"] == 0, o["truth"], -o["rank"]["named"], -o["rank"]["score"]))
    for i, o in enumerate(out):
        o["recommended"] = i == 0  # the strongest line you can truthfully use now
        o["strongest"] = o is strongest  # covers the most of the requirement: always one, often the recommended one
        o["why"] = _draft_why(o)
    return {"requirement": requirement, "suggestions": out}


def _draft_why(o: dict) -> str:
    """Why a draft ranks where it does, in plain words."""
    r = o["rank"]
    parts = [["only rewords your lines", "adds a few words for you to confirm", "has facts only you can fill in"][o["truth"]]]
    if r["covers"]:
        parts.append(f"covers {len(r['covers'])} key words of the requirement ({', '.join(r['covers'])})")
    parts.append("new to this page" if r["dup"] < 0.5 else "close to a line already on the page")
    parts.append("in your experience" if o["in_experience"] else "in a project")
    return ", ".join(parts)


def resume_preview(d: dict, profile: dict) -> dict:
    """Pick approved lines for one job (from the queue, or pasted), print and check the page; nothing is saved yet."""
    facts = engine.load_facts(_facts_path(profile))
    company, title, n, variant, jd = _job_of(d)
    pid = engine.new_preview_id()
    ids = lambda key: set(map(str, d.get(key) or []))
    # your points reworded for this job by Claude; the engine keeps only rewrites that keep your facts
    claude = rewrite.claude_path(profile) if d.get("rewrite", True) and profile.get("rewrite_points", True) else None
    points, ctx, _ = _job_reading(facts, title, jd, company, variant, claude)
    rewrites, note, failed = {}, "", False
    if d.get("rewrite", True) and profile.get("rewrite_points", True):
        if not claude:
            note = "Claude Code isn't installed, so your own wording is used."
        else:
            try:
                rewrites = rewrite.rewrite(points, title, jd, claude, RESULTS / "rewrites", summary_ctx=ctx)
                note = getattr(rewrites, "note", "")
            except rewrite.RewriteError as exc:
                note, failed = f"{exc} Your own wording is used for now.", True
    fit = RESULTS / "fit.json"  # the page size that fit last time, kept across restarts so a page prints once or twice
    try:
        engine._FIT.update(json.loads(fit.read_text()))
    except (OSError, ValueError):
        pass
    out = engine.build(facts, jd, title, variant, RESULTS / "tailor-preview" / pid, None,
                       profile.get("chrome_path") or CHROME_DEFAULT,
                       exclude=ids("exclude"), include=ids("include"), summary_id=d.get("summary_id") or None,
                       title_skip=ids("title_skip"), original=ids("original"), skill_skip=ids("skill_skip"),
                       employer=company, rewrites=rewrites)
    try:
        fit.write_text(json.dumps(engine._FIT))
    except OSError:
        pass
    sel = out["selection"]
    mine, kept = {x["id"]: x["text"] for x in points}, engine.faithful_rewrites(facts, rewrites, sel.get("phrases") or ())
    # the preview shows each line as the page prints it; actions use ids, never this text
    for b in [b for g in sel["experience"] + sel["projects"] for b in g["bullets"]] + list(sel["available"]):
        b["text"] = engine.sentence(b["text"])
        if b.get("original"):
            b["original"] = engine.sentence(b["original"])
    if sel["summary"]:
        sel["summary"] = dict(sel["summary"], text=engine.printed_summary(sel["summary"]["text"]))
    PREVIEWS[pid] = {"pdf": out["pdf"] if not out["failures"] else None, "n": n, "company": company, "title": title}
    while len(PREVIEWS) > 60:
        PREVIEWS.pop(next(iter(PREVIEWS)))
    return {"id": pid, "ok": bool(PREVIEWS[pid]["pdf"]), "failures": out["failures"], "pages": out["pages"],
            "trimmed": out["trimmed"], "title_line": sel["title_line"], "summary": sel["summary"],
            "summaries": [{"id": x["id"], "variant": x.get("variant", "")} for x in facts["summaries"] if engine.ok(x)],
            "experience": sel["experience"], "projects": sel["projects"], "skills": sel["skills"],
            "covered": sel["covered"], "learning": sel["learning"], "not_on_page": sel["not_on_page"], "missing": sel["missing"],
            "skills_detail": sel["skills_detail"], "coverage": sel["coverage"], "available": sel["available"],
            "pending": engine.pending_count(facts), "pending_versions": engine.pending_versions(facts),
            "requirements": sel.get("requirements") or [], "advice": sel.get("advice") or [], "logistics": sel.get("logistics") or [],
            "keywords": sel.get("keywords") or [],
            "places": [{"id": g["id"], "name": g.get("org") or g.get("title", "")} for grp in ("roles", "projects")
                       for g in facts[grp] if engine.ok(g)],
            "rewrite_note": note, "rewrite_failed": failed, "rewritten": len(sel.get("rewrites") or {}),
            "rejected": len([k for k, v in rewrites.items() if k in mine and v != mine[k] and k not in kept]),
            "originals": {k: engine.sentence(mine[k]) for k in sel.get("rewrites") or {} if k in mine}}


WARM = {"note": ""}
_WARM_KICK = threading.Event()


def kick_warmup() -> None:
    """Have the warm-up look ahead again (when the page starts, and after each Mark applied, Skip or Undo)."""
    _WARM_KICK.set()


def warm_ahead(profile: dict) -> int:
    """How many of the next jobs to rewrite ahead of time: `rewrite_warmup` (5 by default, 0 or false for none)."""
    v = profile.get("rewrite_warmup", 5)
    return (5 if v else 0) if isinstance(v, bool) else max(0, int(v or 0))


def warm_rewrites(profile: dict, rounds: int | None = None) -> None:
    """Have Claude rewrite your points ahead of time for the next few jobs in today's queue, one call at a time, so
    Tailor opens in seconds. Each round stops at the first error (signed out, offline); Tailor then says why."""
    ahead, claude = warm_ahead(profile), rewrite.claude_path(profile)
    if not claude or not ahead or not profile.get("rewrite_points", True):
        return
    while rounds is None or rounds > 0:
        _WARM_KICK.wait()
        _WARM_KICK.clear()
        try:
            _warm_round(profile, claude, ahead)
        except Exception as exc:  # never let the warm-up die: Tailor still works without it
            WARM["note"] = f"The warm-up stopped: {exc}"
        if rounds is not None:
            rounds -= 1


def _warm_round(profile: dict, claude: str, ahead: int) -> None:
    queue = json.loads(TODAY.read_text()) if TODAY.exists() else {"items": []}
    st = _state(profile)
    gone = set(st["applied"]) | set(st["skipped"])
    WARM["note"] = ""
    for item in [i for i in queue.get("items", []) if i.get("url") not in gone][:ahead]:
        jd = posting_text(item["url"])
        if len(jd) < 200:
            continue
        facts = engine.load_facts(_facts_path(profile))  # fresh for each job, so lines you just approved count
        try:  # the rewrites for this job, ready before you click Tailor
            points, ctx, _ = _job_reading(facts, item["title"], jd, item["company"], item.get("resume") or variant_for_title(item["title"]), claude)
            rewrite.rewrite(points, item["title"], jd, claude, RESULTS / "rewrites", parallel=False, summary_ctx=ctx)
        except rewrite.RewriteError as exc:
            WARM["note"] = str(exc)
            return


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
        kick_warmup()
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
    claude = rewrite.claude_path(profile)
    if not claude:
        raise ValueError("Reading a LinkedIn page needs Claude Code installed and signed in (claude auth login).")
    try:
        jobs = linkedin.read_page(text, linkedin.clean_links(d.get("links")), claude)
    except rewrite.RewriteError as exc:
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


def resume_save(d: dict, profile: dict) -> dict:
    """Keep a checked preview: Documents (tailored_dir) and a phone copy (tailored_copy_dir)."""
    prev = PREVIEWS.get(str(d.get("id", "")))
    if not prev or not prev["pdf"]:
        raise ValueError("Preview it again; only a preview that passed every check can be saved.")
    folder = tailored_folder(profile) / date.today().isoformat() / job_folder_name(prev)
    root = profile.get("tailored_copy_dir")
    copy_folder = Path(str(root)).expanduser() / date.today().isoformat() / folder.name if root else None
    path, note = engine.keep(prev["pdf"], folder, copy_folder)
    return {"path": path, "note": note}


_DEMAND: dict = {"mtime": None, "counts": {}}


def skill_demand(facts: dict) -> list[dict]:
    """How many of today's jobs name each skill, and where it stands on your resume."""
    if not TODAY.exists():
        return []
    mtime = TODAY.stat().st_mtime
    if _DEMAND["mtime"] != mtime:
        counts: dict[str, int] = {}
        for it in json.loads(TODAY.read_text()).get("items", []):
            for t in engine.terms(engine.without_employer(f"{it.get('title', '')}\n{posting_text(it.get('url', ''))}", it.get("company", ""))):
                if not t.startswith("theme:"):
                    counts[t] = counts.get(t, 0) + 1
        _DEMAND.update(mtime=mtime, counts=counts)
    on_lines = engine._item_terms([it for s in facts["skills"] if engine.ok(s) for it in s["items"]])
    backed = engine.evidence(facts)
    added = {a["term"]: a.get("level") for a in facts.get("added_skills") or [] if engine.ok(a)}
    def state(t):
        return ("on your skills lines" if t in on_lines else "backed by your lines" if t in backed else
                {"used": "added by you", "learning": "on your learning line"}.get(added.get(t), "not added"))
    return [{"term": t, "label": engine.item_label(t), "n": c, "state": state(t)}
            for t, c in sorted(_DEMAND["counts"].items(), key=lambda kv: (-kv[1], kv[0]))]


def facts_view(profile: dict) -> dict:
    """Your facts file for the Resume lines tab, with each suggested version's checks and today's skill demand."""
    facts = engine.load_facts(_facts_path(profile))
    for grp in ("roles", "projects"):
        for g in facts[grp]:
            for b in g["bullets"]:
                for a in b.get("alts") or []:
                    a["problems"] = engine.faithful(b["text"], a)
                    a["adds_text"] = engine.adds_text(a)
    facts["demand"] = skill_demand(facts)
    facts["pending_versions"] = engine.pending_versions(facts)
    return facts


def facts_update(d: dict, profile: dict) -> dict:
    """Approve, remove, restore or reword one of your lines; approve or remove a suggested version; add a skill.
    Rewording is yours alone: a suggested version is used only after you approve it, and only while it keeps your facts."""
    path = _facts_path(profile)
    facts = engine.load_facts(path)
    section, ident, status, text = d.get("section"), d.get("id"), d.get("status"), d.get("text")
    if status not in (None, "approved", "pending", "removed"):
        raise ValueError("Unknown status.")
    stamp = date.today().isoformat()
    if section in ("alt", "alts"):
        if status is None or text is not None:
            raise ValueError("A suggested version can be approved or removed, not edited. Edit your own line instead.")
        found = 0
        for grp in ("roles", "projects"):
            for g in facts[grp]:
                for b in g["bullets"]:
                    for a in b.get("alts") or []:
                        if (section == "alt" and a["id"] == ident) or (section == "alts" and a.get("status") == "pending"):
                            problems = engine.faithful(b["text"], a)
                            if status == "approved" and problems:
                                if section == "alt":
                                    raise ValueError("This version changes a fact, so it can't be approved: " + "; ".join(problems))
                                continue
                            a["status"] = status
                            a["source"] = f"{a.get('source', '')}; {status} by you {stamp}".strip("; ")
                            found += 1
        if section == "alt" and not found:
            raise ValueError("That version isn't in your facts file.")
        engine.save_facts(path, facts)
        return facts_view(profile)
    if section == "new_line":  # a line you write, for work a job asks for that your lines don't show yet
        g = next((g for grp in ("roles", "projects") for g in facts[grp] if g["id"] == str(d.get("group") or "") and engine.ok(g)), None)
        if g is None:
            raise ValueError("Pick one of your roles or projects.")
        clean = " ".join(str(text or "").split())
        if not 20 <= len(clean) <= 450:
            raise ValueError("Write the line in 20 to 450 characters.")
        if any(x in clean for x in engine.BANNED):
            raise ValueError("Use a comma or a period instead of a dash.")
        if engine.NEVER.search(clean):
            raise ValueError(f'"{engine.NEVER.search(clean).group(0)}" never goes on your resume.')
        if "[" in clean or "]" in clean:
            raise ValueError("Fill in the [blanks] with your own facts first.")
        # a line you already have, word for word or nearly, isn't added twice: that one is used instead
        roots = engine._ask_roots(clean)
        twin = next((b for b in g["bullets"] if engine.ok(b) and (b["text"] == clean or (roots and len(roots & engine._ask_roots(b["text"]))
                     / len(roots | engine._ask_roots(b["text"])) >= 0.8))), None)
        if twin is None:
            n = 1 + sum(b["id"].startswith(f"{g['id']}_you") for b in g["bullets"])
            why = str(d.get("requirement") or "")[:160]
            twin = {"id": f"{g['id']}_you{n}", "text": clean, "status": "approved",
                    "source": f"written by you {stamp}" + (f", for: {why}" if why else "")}
            g["bullets"].append(twin)
            engine.save_facts(path, facts)
        return {**facts_view(profile), "added": twin["id"]}  # the page pins it to the resume you're tailoring
    if section == "detail":  # a supporting fact for one of your lines: rewrites may use it for any job
        b = next((b for grp in ("roles", "projects") for g in facts[grp] for b in g["bullets"] if b["id"] == ident), None)
        if b is None or not engine.ok(b):
            raise ValueError("That isn't one of your approved lines.")
        details = b.setdefault("supporting_facts", [])
        if status == "removed":
            i = d.get("index")
            if not isinstance(i, int) or not 0 <= i < len(details):
                raise ValueError("That detail isn't on this line.")
            details[i]["status"] = "removed"
        else:
            clean = " ".join(str(text or "").split())
            if not 10 <= len(clean) <= 300:
                raise ValueError("Keep a detail between 10 and 300 characters.")
            if any(x in clean for x in engine.BANNED):
                raise ValueError("Use a comma or a period instead of a dash.")
            if engine.NEVER.search(clean):
                raise ValueError(f'"{engine.NEVER.search(clean).group(0)}" never goes on your resume.')
            if any(x.get("text") == clean and engine.ok(x) for x in details):
                raise ValueError("This line already has that detail.")
            details.append({"text": clean, "status": "approved", "source": f"added by you {stamp}"})
        engine.save_facts(path, facts)
        return facts_view(profile)
    if section == "impact":
        entry = next((i for i in facts.get("impacts") or [] if i["id"] == ident), None)
        if entry is None or status is None or text is not None:
            raise ValueError("An impact sentence can be approved or removed here.")
        entry["status"] = status
        engine.save_facts(path, facts)
        return facts_view(profile)
    if section == "added_skill":
        term, level = str(d.get("term", "")), d.get("level")
        if term not in engine.vocabulary():
            raise ValueError("That skill isn't one the tailoring knows.")
        if level not in ("used", "learning", "no", "removed"):
            raise ValueError("Answer yes, learning or no.")
        added = facts.setdefault("added_skills", [])
        entry = next((a for a in added if a["term"] == term), None)
        if entry is None:
            entry = {"id": "as_" + term, "term": term, "label": engine.item_label(term)}
            added.append(entry)
        if level == "removed":
            entry["status"] = "removed"
        else:
            entry.update(level=level, status="approved", source=f"{'you said no' if level == 'no' else 'added by you'} {stamp}")
            # where you used it: the rewrite may then name it in those roles' and projects' lines, for every job
            places = {g["id"] for grp in ("roles", "projects") for g in facts[grp]}
            where = [w for w in d.get("where") or [] if isinstance(w, str) and w in places]
            if level == "used" and where:
                entry["where"] = sorted(set(entry.get("where") or []) | set(where))
            elif level != "used":
                entry.pop("where", None)
        engine.save_facts(path, facts)
        return facts_view(profile)
    if section in ("contact", "titles"):
        entry = facts[section]
    elif section in ("summaries", "skills"):
        entry = next((x for x in facts[section] if x["id"] == ident), None)
    elif section == "education":
        entry = facts["education"][int(ident)] if str(ident).isdigit() and int(ident) < len(facts["education"]) else None
    elif section in ("roles", "projects"):
        entry = next((g for g in facts[section] if g["id"] == ident), None)
    elif section == "bullet":
        entry = next((b for grp in ("roles", "projects") for g in facts[grp] for b in g["bullets"] if b["id"] == ident), None)
    else:
        entry = None
    if entry is None:
        raise ValueError("That line isn't in your facts file.")
    if text is not None:
        if section not in ("bullet", "summaries"):
            raise ValueError("Only bullets and summaries can be reworded here.")
        clean = " ".join(str(text).split())
        if any(x in clean for x in engine.BANNED):
            raise ValueError("Use a comma or a period instead of a dash.")
        if not 10 <= len(clean) <= 450:
            raise ValueError("Keep a line between 10 and 450 characters.")
        entry["text"] = clean
        entry["status"] = "approved"
        entry["source"] = f"{entry.get('source', '')}; reworded by you {date.today().isoformat()}".strip("; ")
    if status:
        entry["status"] = status
    engine.save_facts(path, facts)
    return facts_view(profile)


def tailor(item: dict, profile: dict) -> str:
    """Tailor and keep one job's resume as the engine picks it, with no changes from you (tailor-all)."""
    facts = engine.load_facts(_facts_path(profile))
    out = engine.build(facts, posting_text(item["url"]), item["title"], item["resume"],
                       RESULTS / "tailor-preview" / engine.new_preview_id(), None, profile.get("chrome_path") or CHROME_DEFAULT,
                       employer=item["company"])
    if out["failures"] or not out["pdf"]:
        raise ValueError("; ".join(out["failures"]) or "not built")
    folder = tailored_folder(profile) / date.today().isoformat() / job_folder_name(item)
    root = profile.get("tailored_copy_dir")
    copy_folder = Path(str(root)).expanduser() / date.today().isoformat() / folder.name if root else None
    path, note = engine.keep(out["pdf"], folder, copy_folder)
    if note:
        print(f"  ! #{item['n']} {item['company']}: {note}")
    return path


def tailored_folder(profile: dict):
    """Where tailored resumes go: profile `tailored_dir` (for example a folder in Documents), else results/tailored."""
    from pathlib import Path
    return Path(str(profile.get("tailored_dir") or RESULTS / "tailored")).expanduser()


def job_folder_name(item: dict) -> str:
    """'03 Snowflake - Business Intelligence Analyst': safe as a folder name; the PDF inside keeps a clean name."""
    raw = f"{int(item['n']):02d} {item['company']} - {item['title']}"
    return " ".join(re.sub(r"[/\\:*?\"<>|\x00-\x1f]+", " ", raw).split())[:120].rstrip(" .")


def tailor_all(profile: dict, items: list[dict]) -> list[str]:
    """Build a tailored resume for each item (on request only; the page's Tailor button does one at a time)."""
    from pathlib import Path
    made = []
    for it in items:
        try:
            pdf = Path(tailor(it, profile))
        except (ValueError, subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as exc:
            print(f"  ! #{it['n']} {it['company']}: {exc}")
            continue
        made.append(str(pdf))
    return made


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
ESTABLISHED_FILE = Path(__file__).parent / "established.yaml"
_established: set[str] | None = None


def is_startup(company: str, board: str) -> bool:
    """Every employer counts as a startup unless it hires through Workday (enterprises do) or established.yaml
    lists it: public companies and their subsidiaries, decades-old private companies, consultancies, universities,
    hospitals and nonprofits."""
    global _established
    if _established is None:
        _established = set(yaml.safe_load(ESTABLISHED_FILE.read_text()) or []) if ESTABLISHED_FILE.exists() else set()
    return board != "Workday" and company not in _established
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
    rows = _read(POSTINGS)
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
    tmp.replace(TODAY)  # the page and the warm-up never read half a queue
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
    for candidate in range(port, port + 10):  # a page from an earlier run may still hold the port
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
                return self._send(200, page(queue, _state(profile), token, bool(profile.get("resume_facts"))).encode(),
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
                    kick_warmup()
                elif self.path == "/api/skip":
                    skip(str(d.get("company", ""))[:200], str(d.get("title", ""))[:300], url, str(d.get("reason", "")))
                    kick_warmup()
                elif self.path == "/api/undo":
                    undo(url)
                    kick_warmup()
                elif self.path == "/api/followed":
                    followed_up(url, 2 if d.get("which") == 2 else 1)
                elif self.path == "/api/status":
                    update(url, str(d.get("status", "")))
                elif self.path == "/api/resume/preview":
                    return self._send(200, json.dumps(resume_preview(d, profile)).encode())
                elif self.path == "/api/refresh":
                    return self._send(200, json.dumps(start_refresh(profile, d.get("hours", 24))).encode())
                elif self.path == "/api/refresh/status":
                    return self._send(200, json.dumps(dict(REFRESH)).encode())
                elif self.path == "/api/resume/suggest":
                    return self._send(200, json.dumps(resume_suggest(d, profile)).encode())
                elif self.path == "/api/linkedin/check":
                    return self._send(200, json.dumps(linkedin_check(d, profile)).encode())
                elif self.path == "/api/resume/save":
                    return self._send(200, json.dumps(resume_save(d, profile)).encode())
                elif self.path == "/api/facts/get":
                    return self._send(200, json.dumps(facts_view(profile)).encode())
                elif self.path == "/api/facts/update":
                    return self._send(200, json.dumps(facts_update(d, profile)).encode())
                else:
                    return self._send(404, b'{"error":"not found"}')
            except (ValueError, KeyError, subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as exc:
                return self._send(400, json.dumps({"error": str(exc)}).encode())
            return self._send(200, json.dumps(_state(profile)).encode())

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    if profile.get("resume_facts"):  # rewrite the next few jobs in the background so Tailor is quick
        threading.Thread(target=warm_rewrites, args=(profile,), name="warm-rewrites", daemon=True).start()
        kick_warmup()
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


def page(queue: dict, state: dict, token: str, tailor_ok: bool = False) -> str:
    data = json.dumps({"queue": queue, "state": state, "token": token, "reasons": SKIP_REASONS,
                       "statuses": STATUSES, "tailor": tailor_ok, "fields": FIELDS,
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
.tabs{display:flex;gap:6px;padding:0 0 10px;flex-wrap:wrap}.tab.on{background:var(--ink);color:#fff}
button.x{padding:1px 8px;font-size:12px;margin-left:4px}
.pv{border-top:1px dashed var(--line);margin-top:12px;padding-top:12px}
.pv mark{background:#fff1b8;color:inherit;border-radius:3px;padding:0 1px}
.pv h3{font-size:14px;margin:12px 0 4px}.pv ul{margin:4px 0 8px 18px;padding:0}.pv li{margin:3px 0}
.quote{background:var(--bg);border-radius:10px;padding:8px 10px;margin:6px 0}
.line.approved{border-color:#bfe5cc}.line.removed{opacity:.5}.line.bullet{margin-left:22px}
.linetext{margin:6px 0}
.field h3{font-size:16px;margin:22px 0 2px}.field p{margin:0 0 10px}
.chip.learn{background:#e8f0fe;color:#1a56c4}
.wait{background:#fff8ec;border:1px solid #f3dfbf;border-radius:10px;padding:8px 10px;margin:6px 0}
.line.alt{margin-left:44px}
.li-roles{display:flex;flex-wrap:wrap;gap:6px 18px;margin:8px 0;font-size:14px}.li-roles a{margin-left:7px;color:var(--blue);text-decoration:none}
</style></head><body>
<header><div class="wrap bar"><h1>Today</h1><div class="meter"><i id="meter"></i></div>
<div><span class="count" id="count">0</span> <span class="muted">applied today</span></div>
<label class="small muted">Group by <select id="group"><option value="field">field</option><option value="rank">rank only</option><option value="board">job board</option></select></label>
<button id="refresh">Refresh</button><label class="small muted">last <input type="number" id="hours" min="24" max="48" step="1" value="24" aria-label="Hours to keep, 24 to 48" style="width:60px;border:1px solid var(--line);border-radius:8px;padding:5px 6px;font:inherit;font-size:14px"> hours</label>
<button class="primary" id="next">Open next job</button></div>
<div class="wrap tabs"><button class="tab on" data-view="queue">Queue</button><button class="tab" data-view="paste">Paste a job</button><button class="tab" data-view="lines" id="tab-lines">Resume lines</button></div></header>
<main class="wrap">
<div id="status" class="small muted"></div>
<div id="fresh" class="small muted" style="margin:10px 0 0"></div>
<div id="view-queue">
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
</div>
<div id="view-paste" hidden>
<h2>Paste a job</h2>
<div class="card"><div class="row"><input type="text" id="p-company" placeholder="Company"> <input type="text" id="p-title" placeholder="Job title" size="40"></div>
<textarea id="p-jd" placeholder="Paste the whole job description here" style="min-height:220px"></textarea>
<div class="actions"><button class="primary" id="p-go">Preview my resume</button><span class="small muted">Only your approved lines and versions are used.</span></div></div>
<div id="p-preview"></div>
</div>
<div id="view-lines" hidden><h2>Your resume lines</h2><div id="lines"></div></div>
</main>
<script>
const D=/*DATA*/null;let S=D.state;const Q=D.queue;
const $=(id)=>document.getElementById(id);
const STALE='This page is from before the page server restarted. Reload it (Command+R), then try again.';
function el(tag,attrs,...kids){const e=document.createElement(tag);for(const[k,v]of Object.entries(attrs||{})){if(k==='text')e.textContent=v;else if(k==='href'){if(/^https:\\/\\//.test(v))e.href=v;}else if(k.startsWith('on'))e.addEventListener(k.slice(2),v);else e.setAttribute(k,v);}for(const k of kids){if(k!=null)e.append(k);}return e;}
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
function card(it){
 const applied=S.applied[it.url],skipped=S.skipped.includes(it.url);
 const facts=el('div',{class:'facts'},
  el('span',{class:'chip'+(it.age<=3?' new':''),text:ago(it.age,it.age_hours)}),
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
   D.tailor?' ':null,D.tailor?el('button',{onclick:()=>openPreview(it),text:'Tailor to this job'}):null),
  it.mirror.length?el('div',{class:'row'},el('b',{text:'Use their words: '}),document.createTextNode(it.mirror.join(', '))):null,
  it.missing.length?el('div',{class:'row muted'},el('b',{text:'They also want: '}),document.createTextNode(it.missing.join(', ')+'. Mention only what you have really used.')):null,
  it.leverage?el('div',{class:'row'},el('b',{text:'Your angle: '}),document.createTextNode(it.leverage)):null,
  it.grad_check?el('div',{class:'row warn-row'},el('b',{text:'Graduate program: '}),document.createTextNode('check the graduation window before applying. Many 2027 programs want Dec 2026 to mid-2027 grads.')):null,
  it.age>7?el('div',{class:'row warn-row'},el('b',{text:'Over a week old: '}),document.createTextNode('the first reviews may be done. A note to someone there matters more than usual.')):null,
  el('div',{class:'row'},el('b',{text:it.message?'Message one person: ':'Optional, apply-only is fine (invite limit): '}),el('span',{class:'links'},...it.people.map(([t,u])=>el('a',{href:u,target:'_blank',rel:'noopener noreferrer',text:t})))),
  el('div',{class:'row'},name,' ',el('button',{onclick:()=>copy(note.value),text:'Copy message'}),' ',cnt),note,
  actions, el('div',{id:'pv-'+it.n}));
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
 for(const k of Object.keys(PV)) if(k!=='paste') drawPreview(k);
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
  t.textContent=(r.stage||'Starting')+(r.total?': '+r.done+' of '+r.total+' job boards':'')+', keeping the last '+(r.hours||24)+' hours. About 10 minutes; keep applying meanwhile.';return true;}
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
const PV={};
const LENS={'data-engineer':'data engineering','data-analyst':'analytics','data-scientist':'data science','ai-engineer':'AI engineering'};
function newState(extra){return Object.assign({exclude:[],include:[],title_skip:[],summary_id:null,original:[],skill_skip:[]},extra);}
function openPreview(it){const key='q'+it.n;if(PV[key]){delete PV[key];const b=$('pv-'+it.n);if(b)b.replaceChildren();return;}PV[key]={st:newState({n:it.n}),r:null,box:()=>$('pv-'+it.n),label:it.company+', '+it.title};runPreview(key);}
function openPaste(){PV.paste={st:newState({paste:{company:$('p-company').value.trim(),title:$('p-title').value.trim(),jd:$('p-jd').value}}),r:null,box:()=>$('p-preview'),
  label:$('p-company').value.trim()+', '+$('p-title').value.trim()};runPreview('paste');}
async function runPreview(key){const p=PV[key];const box=p&&p.box();if(!box)return;
 const wait=el('p',{class:'muted small',text:p.r?'Updating the page...':'Fitting your points to this job. If Claude hasn\\'t rewritten them for this job yet, this takes up to 40 seconds.'});
 box.replaceChildren(el('div',{class:'pv'},wait));
 try{p.r=await api('/api/resume/preview',p.st);if(p.r.rewrite_failed)p.st.rewrite=false;}catch(e){p.r={error:e.message};}
 drawPreview(key);}
const norm=w=>w.toLowerCase().replace(/^[^a-z0-9$]+|[^a-z0-9%+]+$/g,'');
function marked(text,orig){const have=new Set((orig||'').split(/\\s+/).map(norm));return text.split(/(\\s+)/).map(w=>!norm(w)||have.has(norm(w))?w:el('mark',{title:'Added for this job',text:w}));}
async function factsDo(body,then){try{const f=await api('/api/facts/update',body);setTabCount(f);then(f);}catch(e){flash(e.message,'err');}}
function waitBox(w,redo){return el('div',{class:'wait small'},el('div',{},el('b',{text:'Suggested '+(LENS[w.lens]||'')+' version, waiting for your OK: '})),el('div',{text:w.text}),
 w.adds_text?el('div',{class:'muted',text:'Adds: '+w.adds_text+(w.why?'. Why: '+w.why:'')}):null,
 el('div',{class:'actions'},el('button',{class:'apply',text:'Approve this version',onclick:()=>factsDo({section:'alt',id:w.id,status:'approved'},redo)}),el('button',{text:'Not this one',onclick:()=>factsDo({section:'alt',id:w.id,status:'removed'},redo)})));}
function pinLine(key,f){const st=PV[key]&&PV[key].st;if(st&&f.added){if(!st.include.includes(f.added))st.include.push(f.added);st.exclude=st.exclude.filter(x=>x!==f.added);}
 flash('Added to this resume, and saved for every job it fits','ok');runPreview(key);}
function suggestRows(x,key){  /* drafts for one requirement: one recommended, each labeled by what it adds to your facts */
 if(!x.suggestions.length)return [el('span',{class:'muted',text:'No draft fits your lines for this one. Use "I\\'ve done this" to write it yourself.'})];
 return x.suggestions.map(sg=>{const ta=el('textarea',{rows:'2'});ta.value=sg.text;
  const label=sg.grounded?el('span',{class:'chip new',text:'From your lines'}):el('span',{class:'chip warn',text:'Needs you to confirm: adds '+sg.adds.join('; ')});
  const use=el('button',{class:'apply',text:'Use this line',onclick:()=>{if(/[\\[\\]]/.test(ta.value)){flash('Fill in the [blanks] with your own facts first','err');return;}
    factsDo({section:'new_line',group:sg.place,text:ta.value,requirement:x.requirement},f=>pinLine(key,f));}});
  return el('div',{style:'margin:8px 0'},el('div',{},sg.recommended?el('span',{class:'chip new',text:'Recommended'}):null,sg.strongest?el('span',{class:'chip learn',text:'Strongest'}):null,' ',label,el('span',{class:'muted',text:'  for '+sg.name})),ta,
   sg.why?el('div',{class:'muted',text:(sg.recommended&&sg.strongest?'Why this one, the strongest you can use as is: ':sg.recommended?'Why this one: ':sg.strongest?'The strongest, if it is true for you: ':'Ranked lower: ')+sg.why}):null,
   !sg.grounded?el('div',{class:'muted',text:'Use it only if it is true; saving it is your confirmation.'}):null,el('div',{class:'actions'},use));});}
function suggestButton(q,row,key){const b=el('button',{class:'x',text:'Suggest lines',title:'Two or three drafts built from your own lines, with one recommended'});
 b.addEventListener('click',async()=>{b.disabled=true;const out=el('div',{class:'small',style:'margin:4px 0 8px 58px'},el('span',{class:'muted',text:'Drafting from your lines, about 15 seconds...'}));row.after(out);
  const pr=PV[key].r||{},onPage=(pr.experience||[]).concat(pr.projects||[]).flatMap(g=>g.bullets.map(b=>b.id));
  try{const x=await api('/api/resume/suggest',Object.assign({},PV[key].st,{requirement:q.text,on_page:onPage}));out.replaceChildren(...suggestRows(x,key));}
  catch(e){out.replaceChildren(el('span',{class:'err',text:e.message}));}b.disabled=false;});
 return b;}
function reqBox(r,key){  /* what the job asks for, and which of your lines on this page show it */
 const reqs=r.requirements||[];if(!reqs.length)return null;
 const lines={education:'Education',skills:'Skills',dates:'Your role dates'};for(const g of (r.experience||[]).concat(r.projects||[]))for(const b of g.bullets)lines[b.id]=b.text;
 const full=q=>q.status?q.status==='full':q.by.length>0,part=q=>q.status==='partial';
 const shown=reqs.filter(full).length,reqd=reqs.filter(q=>q.kind==='required'),reqdShown=reqd.filter(full).length,reqdPart=reqd.filter(part).length;
 const box=el('details',{class:'wait',open:'open'},el('summary',{},el('b',{text:'What this job asks for: '+reqdShown+' of '+reqd.length+' required fully on this page'+(reqdPart?' ('+reqdPart+' more partly)':'')+', and '+shown+' of '+reqs.length+' in all'})));
 for(const a of (r.advice||[]))box.append(el('div',{class:'row small'+(a.level==='change'?' warn-row':a.level==='ok'?' ok':' muted')},el('b',{text:a.level==='change'?'Change: ':a.level==='ok'?'Good: ':'Note: '}),a.text));
 box.append(el('p',{class:'small muted',text:"Each line on the page is written toward the requirements it really shows. For one it doesn't show that you have done, write it once with \\"I've done this\\"; it goes on every resume where it fits."}));
 for(const q of reqs){const row=el('div',{class:'row small'});const kind=el('span',{class:'muted',text:' ('+q.kind+')'+(q.by.length&&(q.where||[]).length?', in '+q.where.join(', '):'')});
  const inLines=(q.where||[]).some(w=>w==='experience'||w==='projects');
  const btn=el('button',{class:'x',text:q.by.length?'Show it in a line':"I've done this",title:'Write one line about the work, once; it goes on every resume where it fits'});
  if(q.by.length){const short=part(q)&&q.kind==='required';
   row.append(el('span',{class:part(q)?'chip warn':'chip new',text:part(q)?'partly':'shown'}),' ',q.text,kind,q.kind==='required'&&(!inLines||short)?btn:null,q.kind==='required'&&(!inLines||short)?suggestButton(q,row,key):null,
     part(q)?el('div',{class:'muted',style:'margin-left:58px',text:'short: '+(q.gaps||[]).join('; ')}):null,
     el('div',{class:'muted',style:'margin-left:58px',text:'by: '+q.by.map(id=>{const t=lines[id]||'';return t.length>70?t.slice(0,70)+'...':t;}).join(' | ')}));}
  else row.append(el('span',{class:'chip warn',text:'not shown'}),' ',q.text,kind,' ',btn,suggestButton(q,row,key));
  btn.addEventListener('click',()=>{const where=el('select',{},...(r.places||[]).map(pl=>el('option',{value:pl.id,text:pl.name})));
     const ta=el('textarea',{rows:'2',placeholder:'One line about what you did, in your own words, as it should read on your resume'});
     const form=el('div',{class:'small',style:'margin:4px 0 8px 58px'},el('div',{},'Where: ',where),ta,
       el('div',{class:'actions'},el('button',{class:'apply',text:'Add this line',onclick:()=>factsDo({section:'new_line',group:where.value,text:ta.value,requirement:q.text},f=>pinLine(key,f))}),
         el('button',{text:'Cancel',onclick:()=>form.remove()})));
     row.after(form);ta.focus();});
  box.append(row);}
 if((r.logistics||[]).length)box.append(el('div',{class:'row small muted'},el('b',{text:'Check these yourself, no resume line shows them: '}),r.logistics.map(x=>x.text).join('; ')));
 return box;}
function kwBox(r){  /* the posting's keywords, and where each one stands on this page */
 const ks=r.keywords||[];if(!ks.length)return null;
 const on=ks.filter(k=>k.status==='on page').length;
 const box=el('details',{class:'wait',open:'open'},el('summary',{},el('b',{text:'Keywords from this posting: '+on+' of '+ks.length+' on this page'})));
 const groups=[['on page','On the page','chip new'],['missing','Yours, not on the page yet','chip warn'],
  ['ask','Answer the skills question for these','chip'],['learning','On your Currently learning line','chip'],
  ['declined','You said you have not used these','chip'],['only you','None of your lines shows these; add a line only if you did the work','chip']];
 for(const [st,label,cls] of groups){const xs=ks.filter(k=>k.status===st);if(!xs.length)continue;
  const row=el('div',{class:'row small'},el('b',{text:label+': '}));
  for(const k of xs)row.append(el('span',{class:cls,text:k.text+(k.match==='close'?' (close form)':'')}),' ');
  box.append(row);}
 return box;}
function skillsAsk(r,p,key){  /* every skill this job wants that your resume doesn't show: asked once, remembered for every job */
 const ask=(r.skills_detail||[]).filter(x=>x.state==='missing');if(!ask.length)return null;
 const places=r.places||[];let left=ask.length;
 const update=el('button',{class:'primary',text:'Update this resume with my answers',hidden:'hidden',onclick:()=>runPreview(key)});
 const box=el('div',{class:'wait'},el('b',{text:"This job asks for "+ask.length+(ask.length===1?" skill":" skills")+" your resume doesn't show. Have you used "+(ask.length===1?"it?":"them?")}),
  el('p',{class:'small muted',text:"Yes puts it on your resume for this job and every later job that asks for it, and the rewrite can name it in the places you tick. Learning puts it on a Currently learning line. Every answer is saved, so each skill is asked once."}));
 for(const x of ask){const row=el('div',{class:'row'});
  const save=async(level,where)=>{try{const f=await api('/api/facts/update',{section:'added_skill',term:x.term,level,where:where||[]});setTabCount(f);
     const at=(where||[]).map(w=>(places.find(q=>q.id===w)||{}).name||w);
     row.replaceChildren(el('b',{text:x.label+': '}),el('span',{class:'small',text:level==='used'?'yes'+(at.length?', at '+at.join(', '):', on your skills line'):level==='learning'?'learning':'no, not asked again'}));
     left--;update.hidden=false;if(left<=0)runPreview(key);}
   catch(e){flash(e.message,'err');}};
  const yes=el('button',{class:'apply',text:'Yes',onclick:()=>{const picks=places.map(q=>{const c=el('input',{type:'checkbox',value:q.id});return [c,el('label',{class:'small',style:'margin-right:10px;white-space:nowrap'},c,' '+q.name)];});
     row.replaceChildren(el('b',{text:x.label+': where did you use it? '}),...picks.map(q=>q[1]),
       el('button',{class:'apply',text:'Save',onclick:()=>save('used',picks.filter(q=>q[0].checked).map(q=>q[0].value))}),
       el('span',{class:'small muted',text:' Tick none if it was somewhere else: it goes on your skills line only.'}));}});
  row.append(el('b',{text:x.label+' '}),yes,' ',el('button',{text:'Learning',onclick:()=>save('learning')}),' ',el('button',{text:'No',onclick:()=>save('no')}));
  box.append(row);}
 box.append(el('div',{class:'actions'},update));
 return box;}
function skillChip(x,st,redo){
 if(x.state==='covered')return el('span',{class:'chip new',text:x.label});
 if(x.state==='learning')return el('span',{class:'chip learn',text:x.label+' (learning)'});
 if(x.state==='skipped')return el('span',{class:'chip'},x.label+' (left off) ',el('button',{class:'x',text:'put back',onclick:()=>{st.skill_skip=st.skill_skip.filter(s=>s!==x.item);redo();}}));
 if(x.state==='not_on_page')return el('span',{class:'chip warn',text:x.label+': in your lines, not on this page'});
 if(x.state==='declined')return el('span',{class:'chip'},x.label+' (you said no) ',el('button',{class:'x',title:'Ask me about it again',text:'I have it',onclick:()=>factsDo({section:'added_skill',term:x.term,level:'removed'},redo)}));
 return el('span',{class:'chip'},x.label+' ',el('button',{class:'x',title:'Add it to your skills for every job that asks for it',text:"I've used it",onclick:()=>factsDo({section:'added_skill',term:x.term,level:'used'},redo)}),el('button',{class:'x',title:'Add it to a Currently learning line for jobs that ask for it',text:'learning',onclick:()=>factsDo({section:'added_skill',term:x.term,level:'learning'},redo)}));}
function drawPreview(key){const p=PV[key];const box=p&&p.box();if(!box||!p.r)return;const r=p.r,st=p.st,k=[];
 if(r.error){box.replaceChildren(el('div',{class:'pv'},el('p',{class:'err',text:r.error})));return;}
 const redo=()=>runPreview(key);
 k.push(el('div',{class:'row'},el('b',{text:r.ok?'Ready: one page, every check passed. ':'Not ready yet. '}),r.pending?el('span',{class:'chip warn',text:r.pending+' of your lines still need approval (Resume lines tab)'}):null,' ',r.pending_versions?el('span',{class:'chip warn',text:r.pending_versions+' suggested versions waiting for you'}):null));
 if(r.failures&&r.failures.length)k.push(el('ul',{class:'err'},...r.failures.map(f=>el('li',{text:f}))));
 if(r.rewrite_note)k.push(el('p',{class:'small warn-row',text:r.rewrite_note}));
 const ask=skillsAsk(r,p,key);if(ask)k.push(ask);
 const rq=reqBox(r,key);if(rq)k.push(rq);
 const kb=kwBox(r);if(kb)k.push(kb);
 if(r.rewritten)k.push(el('p',{class:'small muted',text:r.rewritten+' of your points were rewritten for this job, with the added words marked and your wording under each. Read them before you save, and tap "my wording" on any you want back'+(r.rejected?'. '+r.rejected+' rewrites that changed a fact were dropped':'')+'.'}));
 const parts=(r.title_line||'').split(' · ');
 k.push(el('div',{class:'row'},el('b',{text:'Title (names the file): '}),el('span',{class:'chip',text:parts[0]||'(none yet)'})));
 if(r.summary){const job=r.summary.version==='job';const sel=el('select',{},el('option',{value:'',text:job?'written for this job':'best match for this job'}),...r.summaries.map(x=>el('option',{value:x.id,text:'your '+(x.variant||x.id)+' summary'})));sel.value=st.summary_id||'';sel.addEventListener('change',()=>{st.summary_id=sel.value||null;redo();});k.push(el('div',{class:'row'},el('b',{text:'Summary: '}),sel),el('p',{class:'quote',text:r.summary.text}),job?el('p',{class:'small muted',text:'Written for this job from these lines of yours, and fact-checked: '+(r.summary.uses||[]).join(', ')}):null);}
 k.push(el('h3',{text:'Skills on this resume'}));
 for(const s of r.skills||[])k.push(el('div',{class:'row small'},el('b',{text:s.label+': '}),...s.items.map(it=>(s.added||[]).includes(it)?el('span',{class:'chip '+(s.id==='learning'?'learn':'new'),title:s.id==='learning'?'You added it as learning':'Named by this job; your lines back it, or you added it'},it,el('button',{class:'x',title:'Leave it off this resume',onclick:()=>{st.skill_skip.push(it);redo();},text:'✕'})):el('span',{class:'chip',text:it}))));
 for(const [name,groups] of [['Experience',r.experience],['Projects',r.projects]]){if(!groups||!groups.length)continue;k.push(el('h3',{text:name}));
  for(const g of groups){k.push(el('div',{class:'small muted',text:g.heading+(g.dates?' · '+g.dates:'')}));
   k.push(el('ul',{},...g.bullets.map(b=>el('li',{},b.version==='job'?el('span',{},...marked(b.text,(r.originals||{})[b.id])):b.text,b.version==='job'?el('details',{class:'small muted'},el('summary',{text:'rewritten for this job'}),el('div',{text:'Yours: '+((r.originals||{})[b.id]||'')})):b.version!=='original'?el('span',{class:'small muted',text:' ('+(LENS[b.lens]||'job')+' version you approved)'}):null,b.matched.length?el('span',{class:'small muted',text:' (matches: '+b.matched.join(', ')+')'}):null,
    b.version!=='original'?el('button',{class:'x',title:'Use your own wording of this line here',onclick:()=>{st.original.push(b.id);redo();},text:'my wording'}):
     st.original.includes(b.id)?el('button',{class:'x',title:'Go back to the version for this job',onclick:()=>{st.original=st.original.filter(x=>x!==b.id);redo();},text:'job version'}):null,
    detailButton(b.id,redo),
    el('button',{class:'x',title:'Take this line off',onclick:()=>{st.exclude.push(b.id);st.include=st.include.filter(x=>x!==b.id);redo();},text:'✕'}),
    b.waiting?waitBox(b.waiting,redo):null))));}}
 if(r.available&&r.available.length)k.push(el('details',{},el('summary',{text:'Add one of your other approved lines ('+r.available.length+')'}),el('ul',{},...r.available.map(b=>el('li',{},b.version==='job'?el('span',{},...marked(b.text,b.original)):b.text,b.version==='job'?el('details',{class:'small muted'},el('summary',{text:'rewritten for this job'}),el('div',{text:'Yours: '+(b.original||'')})):null,b.matched.length?el('span',{class:'small muted',text:' (matches: '+b.matched.join(', ')+')'}):null,el('button',{class:'x',title:'Put this line on',onclick:()=>{st.include.push(b.id);st.exclude=st.exclude.filter(x=>x!==b.id);redo();},text:'+'}))))));
 k.push(el('div',{class:'row'},el('b',{text:'Their skills: '}),document.createTextNode(r.coverage!=null?r.coverage+'% are on this page'+((r.learning||[]).length?', plus '+r.learning.length+' on your learning line':''):'none detected')));
 k.push(el('div',{class:'facts'},...(r.skills_detail||[]).map(x=>skillChip(x,st,redo))));
 if((r.missing||[]).length)k.push(el('p',{class:'small muted',text:`For a skill the job wants that isn't on your resume: "I've used it" adds it to your skills, and "learning" puts it on a Currently learning line. Either way it stays on your list for every job that asks for it, until you remove it in Resume lines.`}));
 if(r.trimmed&&r.trimmed.length)k.push(el('p',{class:'small muted',text:'Left off to fit one page: '+r.trimmed.join(' | ')}));
 const save=el('button',{class:'apply',text:'Save PDF',onclick:async()=>{save.disabled=true;try{const s2=await api('/api/resume/save',{id:r.id});await navigator.clipboard.writeText(s2.path).catch(()=>{});save.textContent='Saved';flash('Saved, and the path is copied: '+s2.path,'ok');if(s2.note)save.parentNode.after(el('p',{class:'small warn-row',text:s2.note}));}catch(e){flash(e.message,'err');save.disabled=false;}}});
 if(!r.ok)save.disabled=true;
 k.push(el('div',{class:'actions'},save,el('button',{text:'Close',onclick:()=>{delete PV[key];box.replaceChildren();}})));
 box.replaceChildren(el('div',{class:'pv'},...k));}
function countPending(f){let n=0;const c=x=>{if(x.status!=='approved'&&x.status!=='removed')n++;};c(f.contact);c(f.titles);f.summaries.forEach(c);f.education.forEach(c);f.skills.forEach(c);for(const g of f.roles.concat(f.projects)){c(g);g.bullets.forEach(c);}return n;}
function setTabCount(f){const n=countPending(f)+(f.pending_versions||0);$('tab-lines').textContent='Resume lines'+(n?' ('+n+' to review)':'');}
function lineRow(section,id,text,status,source,editable){const row=el('div',{class:'card line '+status+(section==='bullet'?' bullet':'')});
 const upd=async(body)=>{try{drawLines(await api('/api/facts/update',Object.assign({section,id},body)));}catch(e){flash(e.message,'err');}};
 const btns=el('div',{class:'actions'});
 if(status!=='approved')btns.append(el('button',{class:'apply',text:'Approve',onclick:()=>upd({status:'approved'})}));
 if(editable)btns.append(el('button',{text:'Edit',onclick:()=>{const ta=el('textarea',{});ta.value=text;row.replaceChildren(ta,el('div',{class:'actions'},el('button',{class:'apply',text:'Save and approve',onclick:()=>upd({text:ta.value})}),el('button',{text:'Cancel',onclick:loadLines})));}}));
 btns.append(status==='removed'?el('button',{text:'Restore',onclick:()=>upd({status:'pending'})}):el('button',{text:'Remove',onclick:()=>upd({status:'removed'})}));
 row.append(el('div',{class:'row'},el('span',{class:'chip '+(status==='approved'?'new':status==='removed'?'':'warn'),text:status}),el('span',{class:'small muted',text:'  '+(source||'')})),el('div',{class:'linetext',text:text}),btns);return row;}
function altRow(a){const upd=async(s)=>{try{drawLines(await api('/api/facts/update',{section:'alt',id:a.id,status:s}));}catch(e){flash(e.message,'err');}};
 const bad=(a.problems||[]).length,btns=el('div',{class:'actions'});
 if(a.status!=='approved'&&!bad)btns.append(el('button',{class:'apply',text:'Approve',onclick:()=>upd('approved')}));
 btns.append(a.status==='removed'?el('button',{text:'Restore',onclick:()=>upd('pending')}):el('button',{text:a.status==='approved'?'Stop using it':'Remove',onclick:()=>upd('removed')}));
 return el('div',{class:'card line alt '+a.status},el('div',{class:'row'},el('span',{class:'chip '+(a.status==='approved'?'new':a.status==='removed'?'':'warn'),text:a.status==='pending'?'suggested':a.status}),el('span',{class:'small muted',text:'  version for '+(LENS[a.lens]||a.lens)+' jobs'})),
  el('div',{class:'linetext',text:a.text}),a.adds_text?el('div',{class:'small'},el('b',{text:'Adds: '}),document.createTextNode(a.adds_text+(a.why?'. Why: '+a.why:''))):null,
  bad?el('div',{class:'small err',text:"Can't be used: "+a.problems.join('; ')}):null,btns);}
/* details: facts about one of your lines, in your words, that rewrites may use for every job */
const DETAIL_TIP='A fact about this line that rewrites may use for every job: what a metric measured and on what data, the baseline, a framework you used, a test you wrote';
function detailEditor(id,done,cancel){const ta=el('textarea',{rows:'2',placeholder:'A fact about this line, in your own words'});
 const box=el('div',{class:'small'},ta,el('div',{class:'actions'},el('button',{class:'apply',text:'Save detail',onclick:()=>factsDo({section:'detail',id,text:ta.value},done)}),el('button',{text:'Cancel',onclick:()=>cancel(box)})));
 setTimeout(()=>ta.focus(),0);return box;}
function detailButton(id,redo){const b=el('button',{class:'x',title:DETAIL_TIP,text:'+ detail'});
 b.addEventListener('click',()=>b.replaceWith(detailEditor(id,redo,box=>box.replaceWith(b))));return b;}
function detailBox(b){const box=el('div',{class:'small',style:'margin:-6px 0 12px 40px'});
 (b.supporting_facts||[]).forEach((x,i)=>{if(x.status!=='approved')return;
  box.append(el('div',{class:'row'},el('b',{text:'Detail: '}),x.text+' ',el('button',{class:'x',text:'remove',onclick:()=>factsDo({section:'detail',id:b.id,index:i,status:'removed'},drawLines)})));});
 const add=el('button',{class:'x',title:DETAIL_TIP,text:'+ add a detail'});
 add.addEventListener('click',()=>add.replaceWith(detailEditor(b.id,drawLines,ed=>ed.replaceWith(add))));
 box.append(add);return box;}
function skillDo(term,level){factsDo({section:'added_skill',term,level},drawLines);}
function drawLines(f){const k=[],n=countPending(f),nv=f.pending_versions||0;setTabCount(f);
 k.push(el('p',{class:'muted',text:n?n+' lines need your review. Only approved lines ever appear on a tailored resume. Read each one, then Approve it, Edit it or Remove it.':'Every one of your lines is reviewed. Tailored resumes use only the approved ones.'}));
 if(nv)k.push(el('div',{class:'card'},el('p',{text:nv+' suggested versions of your bullets are waiting. Each one says the same work, with the same numbers, in the language of one job type, and anything it adds is listed under it. A version is used only for jobs it fits better than your own wording, and only after you approve it.'}),
  el('div',{class:'actions'},el('button',{class:'apply',text:'Approve all '+nv,onclick:async()=>{if(!confirm('Approve all '+nv+' suggested versions? Each will appear on resumes exactly as written, so read them first.'))return;try{drawLines(await api('/api/facts/update',{section:'alts',status:'approved'}));}catch(e){flash(e.message,'err');}}}),el('span',{class:'small muted',text:'or approve them one by one below'}))));
 k.push(el('h3',{text:'Contact'}),lineRow('contact',null,[f.contact.name].concat(f.contact.lines,f.contact.links.map(l=>l.text)).join(' | '),f.contact.status,f.contact.source,false));
 k.push(el('h3',{text:'Titles allowed on your title line'}),lineRow('titles',null,f.titles.items.join(' · '),f.titles.status,f.titles.source,false));
 k.push(el('h3',{text:'Summaries'}));f.summaries.forEach(x=>k.push(x.opener?lineRow('summaries',x.id,x.opener.replace('{title}','[your title for the job]')+' [the impact sentence that fits the job] '+(x.closer||''),x.status,(x.variant||'')+' · '+x.source,false):lineRow('summaries',x.id,x.text,x.status,(x.variant||'')+' · '+x.source,true)));
 if((f.impacts||[]).length){k.push(el('h3',{text:'Impact sentences (each summary uses the one that fits the job)'}));f.impacts.forEach(x=>k.push(lineRow('impact',x.id,x.text,x.status,'from your line '+x.backs+' · '+(x.lens||[]).join(', '),false)));}
 k.push(el('h3',{text:'Education'}));f.education.forEach((x,i)=>k.push(lineRow('education',String(i),x.school+' | '+x.degree+' | '+x.date,x.status,x.source,false)));
 k.push(el('h3',{text:'Skills'}));f.skills.forEach(x=>k.push(lineRow('skills',x.id,x.label+': '+x.items.join(' · '),x.status,x.source,false)));
 k.push(el('p',{class:'small muted',text:'A tailored resume also lists any skill a job names that your approved lines show you used, such as a tool named in one of your bullets.'}));
 const added=(f.added_skills||[]).filter(a=>a.status==='approved'&&a.level!=='no'),saidNo=(f.added_skills||[]).filter(a=>a.status==='approved'&&a.level==='no');
 const placeName=id=>{const g=(f.roles||[]).concat(f.projects||[]).find(g=>g.id===id);return g?(g.org||g.title||id):id;};
 k.push(el('h3',{text:'Skills you added'}));
 if(!added.length)k.push(el('p',{class:'small muted',text:'None yet. Add them from a job preview, or from the list below.'}));
 for(const a of added)k.push(el('div',{class:'row'},el('span',{class:'chip '+(a.level==='learning'?'learn':'new'),text:a.label+(a.level==='learning'?' (learning)':'')+(a.where&&a.where.length?' at '+a.where.map(placeName).join(', '):'')}),' ',
  el('button',{class:'x',text:a.level==='learning'?"I've used it now":'move to learning',onclick:()=>skillDo(a.term,a.level==='learning'?'used':'learning')}),el('button',{class:'x',text:'remove',onclick:()=>skillDo(a.term,'removed')})));
 if(saidNo.length)k.push(el('p',{class:'small muted'},'You said no to: ',...saidNo.map(a=>el('span',{class:'chip'},a.label+' ',el('button',{class:'x',text:'ask again',onclick:()=>skillDo(a.term,'removed')})))));
 const dm=(f.demand||[]).filter(x=>x.n>=2).slice(0,45);
 if(dm.length){k.push(el('h3',{text:"What today's jobs ask for"}),el('p',{class:'small muted',text:"How many of today's jobs name each skill. Green is on your resume already; add the others you can talk about in an interview."}));
  k.push(el('div',{class:'facts'},...dm.map(x=>x.state==='not added'?el('span',{class:'chip'},x.label+' · '+x.n+' ',el('button',{class:'x',text:"I've used it",onclick:()=>skillDo(x.term,'used')}),el('button',{class:'x',text:'learning',onclick:()=>skillDo(x.term,'learning')})):el('span',{class:'chip '+(x.state==='on your learning line'?'learn':'new'),title:x.state,text:x.label+' · '+x.n}))));}
 for(const [grp,name] of [['roles','Experience'],['projects','Projects']]){k.push(el('h3',{text:name}));
  for(const g of f[grp]){k.push(lineRow(grp,g.id,grp==='roles'?g.title+' | '+g.org+(g.place?' · '+g.place:'')+' | '+g.dates:g.title+' | '+g.subtitle,g.status,g.source,false));
   g.bullets.forEach(b=>{k.push(lineRow('bullet',b.id,b.text,b.status,b.source,true));if(b.status==='approved')k.push(detailBox(b));(b.alts||[]).forEach(a=>k.push(altRow(a)));});}}
 $('lines').replaceChildren(...k);}
async function loadLines(){$('lines').replaceChildren(el('p',{class:'muted',text:'Loading your lines...'}));try{drawLines(await api('/api/facts/get',{}));}catch(e){$('lines').replaceChildren(el('p',{class:'err',text:e.message}));}}
for(const b of document.querySelectorAll('.tab'))b.addEventListener('click',()=>{for(const t of document.querySelectorAll('.tab'))t.classList.toggle('on',t===b);for(const v of ['queue','paste','lines'])$('view-'+v).hidden=(v!==b.dataset.view);if(b.dataset.view==='lines')loadLines();});
$('p-go').addEventListener('click',openPaste);
if(D.tailor)api('/api/facts/get',{}).then(setTabCount).catch(()=>{});
render();
</script></body></html>
"""
